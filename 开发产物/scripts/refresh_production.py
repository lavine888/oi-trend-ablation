"""生产产物刷新：把 `数据库.parquet` 更新到最新交易日。

为什么不直接改 study 面板
-------------------------
预注册把研究窗口冻结为 2016–2025（IS 2016–2021 / OOS 2022–2025）。刷新生产产物若改写
同一张面板，会让 OOS 结果不可复现。因此本脚本另走一条路：

1. 读取已冻结的研究面板（2016-2025）与 `output/universe.json` 里冻结的品种表；
2. 只增量拉取「研究窗口之后」的日线，并往前多取 3 个月作为动量/持仓回看；
3. 用与 `factor.py` **完全相同**的特征与权重函数计算；
4. 把最新有效交易日的结果写成 `生产产物/数据库.parquet`，并在 manifest 里注明
   生产日期与研究窗口的边界。

运行
----
    python scripts/refresh_production.py                # 拉到今天
    python scripts/refresh_production.py --end 20261113
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pd_client  # noqa: E402
import factor as ft  # noqa: E402
import build_production as bp  # noqa: E402

OUT = Path(os.getenv("ALPHA61_OUT") or (HERE.parent / "output"))
PROD = Path(os.getenv("ALPHA61_PROD") or (HERE.parent.parent / "生产产物"))
CST = timezone(timedelta(hours=8))
LOOKBACK_START = "20250901"   # 比研究窗口末尾多留 ~4 个月，保证 60 日动量与 ΔOI_5d 可算


def _to_iso(s: pd.Series) -> pd.Series:
    """把 YYYYMMDD / YYYY-MM-DD 统一成 YYYY-MM-DD（混排会让 sort 与 shift 错位）。"""
    return pd.to_datetime(s.astype(str).str.replace("-", "", regex=False),
                          format="%Y%m%d", errors="coerce").dt.strftime("%Y-%m-%d")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", default=date.today().strftime("%Y%m%d"))
    args = ap.parse_args()

    uni = json.loads((OUT / "universe.json").read_text(encoding="utf-8"))["included"]
    panel = pd.read_parquet(OUT / "panel.parquet")
    study_max = str(panel["date"].max())
    print(f"研究面板 {len(panel)} 行，止于 {study_max}；冻结宇宙 {len(uni)} 个品种")

    pd_client.bootstrap()
    new = pd_client.future_daily(uni, LOOKBACK_START, args.end)
    if not len(new):
        print("平台未返回增量数据")
        return 1
    print(f"增量拉取 {len(new)} 行，{new['date'].min()} ~ {new['date'].max()}")

    full = (pd.concat([panel, new], ignore_index=True)
            .drop_duplicates(subset=["symbol", "date"])
            .sort_values(["symbol", "date"]).reset_index(drop=True))
    # 统一日期格式后再建特征，避免 YYYYMMDD 与 ISO 混排导致的排序/位移错位
    full["date"] = _to_iso(full["date"])
    full = (full.dropna(subset=["date"])
            .drop_duplicates(subset=["symbol", "date"])
            .sort_values(["symbol", "date"]).reset_index(drop=True))

    feat = ft.build_features(full)
    sig = ft.build_signals(feat, uni)
    sig["rebalance"] = False
    for sym in uni:
        idx = sig.index[sig["symbol"] == sym]
        if len(idx):
            sig.loc[idx[:: ft.REBALANCE_DAYS], "rebalance"] = True

    # 最新「已结算」交易日：当日 settlement 未发布时 r 为空，不能出信号
    coverage = feat.groupby("date")["r"].apply(lambda s: float(s.notna().mean()))
    settled = [d for d, v in coverage.items() if v >= 0.8]
    if not settled:
        raise SystemExit("没有覆盖率达标的已结算交易日")
    latest = max(settled)
    print(f"最新已结算交易日 {latest}（覆盖率 {coverage[latest]:.0%}）")

    sha = json.loads((OUT / "panel_manifest.json").read_text(encoding="utf-8"))["sha1"]
    df = bp.build(sig, latest, sha)
    checks = bp.validate(df)
    if not checks["all_passed"]:
        raise SystemExit(f"生产质量检查未通过：{json.dumps(checks, ensure_ascii=False)}")

    PROD.mkdir(parents=True, exist_ok=True)
    target = PROD / "数据库.parquet"
    df.to_parquet(target, index=False)
    readback = pd.read_parquet(target)

    manifest = {
        "generated_at": datetime.now(CST).isoformat(timespec="seconds"),
        "factor_id": bp.FACTOR_ID,
        "factor_name": bp.FACTOR_NAME,
        "trade_date": latest,
        "rows": int(len(readback)),
        "symbols": int(readback["symbol"].nunique()),
        "data_version": df["data_version"].iloc[0],
        "study_window": f"{str(panel['date'].min())} ~ {study_max}",
        "production_window": f"{new['date'].min()} ~ {latest}",
        "refresh_end_requested": args.end,
        "source": "panda_data.get_future_daily",
        "entry_type": "panda_data_api",
        "panel_sha1": sha,
        "quality_checks": checks,
        "readback_ok": bool(len(readback) == len(df)),
        "signal_counts": df["signal"].value_counts().to_dict(),
        "note": "研究窗口冻结不变；本文件只把最新交易日的信号刷新到当期",
    }
    (PROD / "build_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2)[:900])
    print(f"\n写出 {target}（{len(df)} 行，交易日 {latest}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
