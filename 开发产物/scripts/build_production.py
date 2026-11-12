"""Alpha-61 生产产物生成器：把最新有效交易日的因子结果落成 `数据库.parquet`。

对应《Alpha 因子开发与生产规则 V2》第 7–10 节：交易 agent 不重复拉数、不重算因子，
只按 `trade_date / symbol / factor_id` 读取。

运行
----
    python scripts/build_production.py
    python scripts/build_production.py --date 2025-12-31   # 指定历史某日重建

产出
----
    生产产物/数据库.parquet
    生产产物/build_manifest.json   （数据版本、校验结果、行数、来源面板哈希）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

ROOT = HERE.parent
OUT = Path(os.getenv("ALPHA61_OUT") or (ROOT / "output"))
PROD = Path(os.getenv("ALPHA61_PROD") or (ROOT.parent / "生产产物"))
CST = timezone(timedelta(hours=8))

FACTOR_ID = "F61"
FACTOR_NAME = "OIConfirmedTrend"
SIGNAL_ENUM = {"buy", "sell", "hold"}


def data_version(panel_sha: str, trade_date: str) -> str:
    return f"{FACTOR_ID}-{trade_date.replace('-', '')}-{panel_sha[:8]}"


def pick_date(sig: pd.DataFrame, want: str | None) -> str:
    dates = sorted(sig["date"].unique())
    if want:
        if want not in dates:
            raise SystemExit(f"指定日期 {want} 不在面板中；最近可用：{dates[-1]}")
        return want
    return dates[-1]


def build(sig: pd.DataFrame, trade_date: str, panel_sha: str) -> pd.DataFrame:
    d = sig[sig["date"] == trade_date].copy()
    if d.empty:
        raise SystemExit(f"{trade_date} 无数据")

    tradable = d["tradable"].fillna(False)
    # 因子值 = 带门的趋势强度（未确认置 0，与生产信号同源）
    d["factor_value"] = np.where(tradable, d["mom60"] * np.sign(d["gate_oi"] - 0.5).clip(0, 1), 0.0)
    d["factor_value"] = d["factor_value"].fillna(0.0)

    strength = d["mom60"].abs()
    d["score"] = strength.rank(pct=True).mul(100).round(2).fillna(0.0)

    def to_signal(w: float) -> str:
        if w > 0:
            return "buy"
        if w < 0:
            return "sell"
        return "hold"

    d["signal"] = d["w_b1"].apply(to_signal)

    # 置信度：通过持仓确认时取 |ΔOI| 的横截面分位，未通过为 0
    oi_abs = d["oi_chg5"].abs()
    pct = oi_abs.rank(pct=True).fillna(0.0)
    d["confidence"] = np.where(d["gate_oi"] == 1, pct, 0.0).round(4)

    d["rank"] = strength.rank(ascending=False, method="min").astype("Int64")

    fv = np.array(d["factor_value"].round(8).tolist(), dtype="float64")
    fv[np.abs(fv) < 1e-12] = 0.0   # 去掉 -0.0 这种显示噪音
    out = pd.DataFrame({
        "trade_date": trade_date,
        "asset_type": "future",
        "symbol": d["symbol"].values,
        "factor_id": FACTOR_ID,
        "factor_name": FACTOR_NAME,
        "factor_value": fv,
        "score": d["score"].values,
        "rank": d["rank"].values,
        "signal": d["signal"].values,
        "confidence": d["confidence"].values,
        "data_version": data_version(panel_sha, trade_date),
        "update_time": datetime.now(CST).isoformat(timespec="seconds"),
    })
    return out.sort_values("rank").reset_index(drop=True)


def validate(df: pd.DataFrame) -> dict:
    """第 10 节生产数据质量检查。任何一项失败都必须中止而不是写出坏结果。"""
    checks = {
        "primary_key_unique": not df.duplicated(subset=["trade_date", "symbol", "factor_id"]).any(),
        "no_null_required": bool(df[["trade_date", "asset_type", "symbol", "factor_id", "factor_name",
                                     "factor_value", "score", "signal", "data_version", "update_time"]]
                                 .notna().all().all()),
        "signal_enum_valid": set(df["signal"]).issubset(SIGNAL_ENUM),
        "score_in_range": bool(df["score"].between(0, 100).all()),
        "dates_iso": bool(df["trade_date"].str.match(r"^\d{4}-\d{2}-\d{2}$").all()),
        "single_trade_date": df["trade_date"].nunique() == 1,
        "data_version_single": df["data_version"].nunique() == 1,
        "has_rows": len(df) > 0,
    }
    checks["all_passed"] = all(checks.values())
    return checks


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    args = ap.parse_args()

    sig = pd.read_parquet(OUT / "signals.parquet")
    sig["date"] = sig["date"].astype(str)

    manifest_path = OUT / "panel_manifest.json"
    panel_sha = json.loads(manifest_path.read_text(encoding="utf-8"))["sha1"] if manifest_path.exists() else "unknown"

    trade_date = pick_date(sig, args.date)
    df = build(sig, trade_date, panel_sha)
    checks = validate(df)
    if not checks["all_passed"]:
        raise SystemExit(f"生产数据质量检查未通过，拒绝写出：{json.dumps(checks, ensure_ascii=False)}")

    PROD.mkdir(parents=True, exist_ok=True)
    target = PROD / "数据库.parquet"
    df.to_parquet(target, index=False)

    readback = pd.read_parquet(target)
    manifest = {
        "generated_at": datetime.now(CST).isoformat(timespec="seconds"),
        "factor_id": FACTOR_ID,
        "factor_name": FACTOR_NAME,
        "trade_date": trade_date,
        "rows": int(len(readback)),
        "symbols": int(readback["symbol"].nunique()),
        "columns": list(readback.columns),
        "data_version": df["data_version"].iloc[0],
        "panel_sha1": panel_sha,
        "quality_checks": checks,
        "readback_ok": bool(len(readback) == len(df)),
        "parquet_sha256": hashlib.sha256(target.read_bytes()).hexdigest()[:16],
        "signal_counts": df["signal"].value_counts().to_dict(),
    }
    (PROD / "build_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2)[:1200])
    print(f"\n写出 {target}（{len(df)} 行）")


if __name__ == "__main__":
    main()
