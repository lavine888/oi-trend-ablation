"""Alpha-61 因子计算主脚本：持仓确认趋势（OI-Confirmed Trend）。

运行
----
    python scripts/factor.py                 # 拉数据 + 建面板 + 出信号
    python scripts/factor.py --reuse-panel   # 复用已下载面板，只重算信号

产出
----
    开发产物/output/panel.parquet        原始面板（平台字段原样保留）
    开发产物/output/panel_manifest.json  数据版本、行数、日期范围、内容哈希、接口留痕摘要
    开发产物/output/signals.parquet      逐日逐品种特征与权重（B0 / B1 / P1）
    开发产物/output/universe.json        冻结宇宙与纳入/剔除原因

口径一律以 `00_预注册_研究设计与口径.md` 为准；本脚本不得引入预注册之外的可调参数。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pd_client  # noqa: E402

ROOT = HERE.parent
OUT = Path(os.getenv("ALPHA61_OUT") or (ROOT / "output"))
CST = timezone(timedelta(hours=8))

FACTOR_ID = "F61"
FACTOR_NAME = "OIConfirmedTrend"

# --- 预注册常量（改这里等于改预注册，必须同步修订记录） ---
LOOKBACK = 60          # 趋势回看交易日
OI_LOOKBACK = 5        # 持仓变化回看交易日
REBALANCE_DAYS = 5     # 调仓间隔（交易日）
IS_START, IS_END = "2016-01-01", "2021-12-31"
OOS_START, OOS_END = "2022-01-01", "2025-12-31"
MIN_ROWS_FULL = 500      # 全窗口最少有效交易日
MIN_AMOUNT_IS = 5e8      # IS 日成交额中位数下限（元）
MIN_COVERAGE_IS = 0.80   # IS 交易日覆盖率下限

# 候选宇宙：只声明候选，是否纳入由 IS 规则决定（避免事后挑选）
CANDIDATES: List[Tuple[str, str]] = [
    # 上期所 SHF
    ("CU", "SHF"), ("AL", "SHF"), ("ZN", "SHF"), ("PB", "SHF"), ("NI", "SHF"), ("SN", "SHF"),
    ("AU", "SHF"), ("AG", "SHF"), ("RB", "SHF"), ("HC", "SHF"), ("SS", "SHF"), ("BU", "SHF"),
    ("RU", "SHF"), ("FU", "SHF"), ("SP", "SHF"),
    # 大商所 DCE
    ("A", "DCE"), ("B", "DCE"), ("M", "DCE"), ("Y", "DCE"), ("P", "DCE"), ("C", "DCE"),
    ("CS", "DCE"), ("JD", "DCE"), ("L", "DCE"), ("V", "DCE"), ("PP", "DCE"), ("EG", "DCE"),
    ("J", "DCE"), ("JM", "DCE"), ("I", "DCE"), ("PG", "DCE"), ("LH", "DCE"), ("FB", "DCE"), ("RR", "DCE"),
    # 郑商所 CZC
    ("SR", "CZC"), ("CF", "CZC"), ("CY", "CZC"), ("TA", "CZC"), ("MA", "CZC"), ("OI", "CZC"),
    ("RM", "CZC"), ("RS", "CZC"), ("SF", "CZC"), ("SM", "CZC"), ("AP", "CZC"), ("CJ", "CZC"),
    ("PK", "CZC"), ("PF", "CZC"), ("SA", "CZC"), ("UR", "CZC"), ("FG", "CZC"),
    # 能源中心 INE
    ("SC", "INE"), ("NR", "INE"), ("LU", "INE"), ("BC", "INE"),
    # 广期所 GFX
    ("SI", "GFX"), ("LC", "GFX"),
]

CHUNKS = [("20160101", "20201231"), ("20210101", "20251231")]

REQUIRED_COLS = ["symbol", "date", "settlement", "pre_settlement", "open_interest", "volume", "amount"]


def now() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def sha1_of(df: pd.DataFrame) -> str:
    return hashlib.sha1(pd.util.hash_pandas_object(df, index=True).values.tobytes()).hexdigest()


def symbol_of(variety: str, ex: str) -> str:
    return f"{variety}_DOMINANT.{ex}"


def fetch_panel(symbols: List[str], log: List[dict]) -> pd.DataFrame:
    """按 ≤5 年分段批量拉取；整批失败则逐品种回退，全部失败写入 log。"""
    frames: List[pd.DataFrame] = []
    for start, end in CHUNKS:
        try:
            df = pd_client.future_daily(symbols, start, end)
            frames.append(df)
            log.append({"chunk": [start, end], "mode": "batch", "rows": len(df)})
            print(f"  [chunk {start}-{end}] 批量成功 {len(df)} 行")
            continue
        except Exception as exc:  # noqa: BLE001
            log.append({"chunk": [start, end], "mode": "batch", "error": f"{type(exc).__name__}: {exc}"})
            print(f"  [chunk {start}-{end}] 批量失败（{type(exc).__name__}），逐品种回退")

        for i, sym in enumerate(symbols, 1):
            try:
                df = pd_client.future_daily([sym], start, end)
                if len(df):
                    frames.append(df)
                    log.append({"chunk": [start, end], "mode": "single", "symbol": sym, "rows": len(df)})
            except Exception as exc:  # noqa: BLE001
                log.append({"chunk": [start, end], "mode": "single", "symbol": sym,
                            "error": f"{type(exc).__name__}: {exc}"})
            if i % 10 == 0:
                print(f"  [chunk {start}-{end}] 逐品种进度 {i}/{len(symbols)}，累计 {sum(len(f) for f in frames)} 行")

    if not frames:
        raise RuntimeError("平台未返回任何期货日线数据，检查登录状态与合约代码")

    panel = pd.concat(frames, ignore_index=True)
    panel = panel.drop_duplicates(subset=["symbol", "date"]).sort_values(["symbol", "date"]).reset_index(drop=True)
    return panel


def build_features(panel: pd.DataFrame) -> pd.DataFrame:
    """逐品种构造收益、趋势、持仓变化与波动。所有特征只用当日及以前数据。"""
    panel = panel.copy()
    # 平台 date 为 YYYYMMDD；统一成 ISO（生产字段要求，且便于按年切片）
    d = panel["date"].astype(str).str.replace("-", "", regex=False)
    panel["date"] = pd.to_datetime(d, format="%Y%m%d", errors="coerce").dt.strftime("%Y-%m-%d")
    panel = panel.dropna(subset=["date"])
    panel["r"] = panel["settlement"] / panel["pre_settlement"] - 1.0
    panel["oi"] = pd.to_numeric(panel["open_interest"], errors="coerce")
    panel["amt"] = pd.to_numeric(panel["amount"], errors="coerce")
    panel["vol"] = pd.to_numeric(panel["volume"], errors="coerce")

    out = []
    for sym, g in panel.groupby("symbol", sort=True):
        g = g.sort_values("date").copy()
        lr = np.log1p(g["r"].clip(lower=-0.99))
        g["mom60"] = lr.rolling(LOOKBACK, min_periods=LOOKBACK).sum()
        g["oi_chg5"] = g["oi"] / g["oi"].shift(OI_LOOKBACK) - 1.0
        g["vol_chg5"] = g["vol"] / g["vol"].shift(OI_LOOKBACK) - 1.0
        g["vol60"] = g["r"].rolling(LOOKBACK, min_periods=LOOKBACK).std() * np.sqrt(252)
        out.append(g)
    feat = pd.concat(out, ignore_index=True)
    # 分母为 0 会产出 ±inf（例如新合约上市首日 OI 前值为 0）：一律视为缺失，不参与交易
    for c in ("r", "oi_chg5", "vol_chg5", "mom60", "vol60"):
        feat[c] = feat[c].replace([np.inf, -np.inf], np.nan)
    feat["underlying"] = feat["symbol"].str.split("_").str[0]
    return feat


def select_universe(feat: pd.DataFrame) -> Tuple[List[str], List[dict]]:
    """只用 IS 窗口执行预注册纳入规则；返回冻结宇宙与逐品种判定原因。"""
    dates_is = feat.loc[(feat.date >= IS_START) & (feat.date <= IS_END), "date"].nunique()
    rows: List[dict] = []
    keep: List[str] = []
    for sym, g in feat.groupby("symbol", sort=True):
        gi = g[(g.date >= IS_START) & (g.date <= IS_END)]
        valid_full = int(g["r"].notna().sum())
        valid_is = int(gi["r"].notna().sum())
        cov_is = (gi["date"].nunique() / dates_is) if dates_is else 0.0
        amt_med = float(gi.loc[gi["amt"].notna(), "amt"].median()) if gi["amt"].notna().any() else 0.0
        reasons = []
        if valid_full < MIN_ROWS_FULL:
            reasons.append(f"valid_rows<{MIN_ROWS_FULL}({valid_full})")
        if amt_med < MIN_AMOUNT_IS:
            reasons.append(f"is_amount_median<{MIN_AMOUNT_IS:.0e}({amt_med:.3e})")
        if cov_is < MIN_COVERAGE_IS:
            reasons.append(f"is_coverage<{MIN_COVERAGE_IS}({cov_is:.3f})")
        ok = not reasons
        rows.append({"symbol": sym, "included": ok, "valid_rows": valid_full, "valid_rows_is": valid_is,
                     "is_coverage": round(cov_is, 4), "is_amount_median": amt_med, "reasons": reasons})
        if ok:
            keep.append(sym)
    return keep, rows


def build_signals(feat: pd.DataFrame, universe: List[str]) -> pd.DataFrame:
    """构造 B0 / B1 / P1 权重。信号 t 日收盘确定，t+1 起计收益。"""
    f = feat[feat["symbol"].isin(universe)].copy()
    n = len(universe)
    if n == 0:
        raise RuntimeError("冻结宇宙为空，中止")
    slot = 1.0 / n

    f["s"] = np.sign(f["mom60"])
    f["gate_oi"] = (np.sign(f["oi_chg5"]) == f["s"]).astype(float)
    f["gate_vol"] = (np.sign(f["vol_chg5"]) == f["s"]).astype(float)
    # 特征缺失时不可交易
    tradable = f[["mom60", "oi_chg5", "vol_chg5"]].notna().all(axis=1)
    f["tradable"] = tradable

    f["w_b0"] = np.where(tradable, f["s"] * slot, 0.0)
    f["w_b1"] = np.where(tradable, f["s"] * slot * f["gate_oi"], 0.0)
    f["w_p1"] = np.where(tradable, f["s"] * slot * f["gate_vol"], 0.0)
    return f


def _write_manifest(panel: pd.DataFrame, call_log: List[dict], entry_type: str) -> None:
    manifest = {
        "generated_at": now(),
        "entry_type": entry_type,
        "source": "panda_data.get_future_daily" if entry_type == "panda_data_api" else "external_csv_export",
        "provenance_note": ("平台接口直取" if entry_type == "panda_data_api"
                            else "外部 CSV：平台真实性弱于接口直取，结论须披露"),
        "rows": int(len(panel)),
        "symbols_returned": int(panel["symbol"].nunique()),
        "date_min": str(panel["date"].min()),
        "date_max": str(panel["date"].max()),
        "columns": list(panel.columns),
        "sha1": sha1_of(panel),
        "calls": call_log,
    }
    (OUT / "panel_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[manifest] entry_type={entry_type} sha1={manifest['sha1']}")


def load_panel_csv(path: Path) -> pd.DataFrame:
    """备用入口：读取平台导出 CSV（字段须与 get_future_daily 同名）。"""
    df = pd.read_csv(path)
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise SystemExit(f"CSV 缺少必需字段 {missing}；必需字段 {REQUIRED_COLS}")
    df["date"] = df["date"].astype(str)
    for c in ("settlement", "pre_settlement", "open_interest", "volume", "amount"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if "dominant_id" not in df.columns:
        df["dominant_id"] = df["symbol"]
    return df.drop_duplicates(subset=["symbol", "date"]).sort_values(["symbol", "date"]).reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-panel", action="store_true")
    ap.add_argument("--panel-csv", default=None, help="备用：平台导出 CSV（manifest 会标记为外部导出）")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    call_log: List[dict] = []
    entry_type = "panda_data_api"
    if args.panel_csv:
        panel = load_panel_csv(Path(args.panel_csv))
        entry_type = "external_csv"
        print(f"[panel] 外部 CSV {len(panel)} 行（平台真实性弱于接口直取，写入 manifest 标记）")
        panel.to_parquet(OUT / "panel.parquet", index=False)
        _write_manifest(panel, call_log, entry_type)
        print(f"[panel] {len(panel)} 行 / {panel['symbol'].nunique()} 合约序列")
    elif args.reuse_panel and (OUT / "panel.parquet").exists():
        panel = pd.read_parquet(OUT / "panel.parquet")
        print(f"[panel] 复用本地面板 {len(panel)} 行")
    else:
        symbols = [symbol_of(v, e) for v, e in CANDIDATES]
        print(f"[fetch] 候选 {len(symbols)} 个主力连续序列，分段 {CHUNKS}")
        panel = fetch_panel(symbols, call_log)
        panel.to_parquet(OUT / "panel.parquet", index=False)
        _write_manifest(panel, call_log, entry_type)
        print(f"[panel] {len(panel)} 行 / {panel['symbol'].nunique()} 合约序列")

    feat = build_features(panel)
    universe, audit = select_universe(feat)
    (OUT / "universe.json").write_text(
        json.dumps({"frozen_at": now(), "rules": {"min_rows_full": MIN_ROWS_FULL,
                                                  "min_is_amount_median": MIN_AMOUNT_IS,
                                                  "min_is_coverage": MIN_COVERAGE_IS},
                    "included": universe, "audit": audit}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[universe] 纳入 {len(universe)} 个品种：{', '.join(sorted(universe))}")

    sig = build_signals(feat, universe)
    sig["rebalance"] = False
    for sym in universe:
        idx = sig.index[sig["symbol"] == sym]
        if len(idx):
            sig.loc[idx[::REBALANCE_DAYS], "rebalance"] = True
    sig.to_parquet(OUT / "signals.parquet", index=False)
    print(f"[signals] {len(sig)} 行，列 {list(sig.columns)}")

    # 数据质量摘要（不参与调参，只做披露）
    q = {
        "rows": int(len(sig)),
        "symbols": len(universe),
        "missing_mom60": int(sig["mom60"].isna().sum()),
        "missing_oi_chg5": int(sig["oi_chg5"].isna().sum()),
        "tradable_share": round(float(sig["tradable"].mean()), 4),
        "gate_oi_pass_rate": round(float(sig.loc[sig.tradable, "gate_oi"].mean()), 4),
    }
    (OUT / "factor_quality.json").write_text(json.dumps(q, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[quality]", q)


if __name__ == "__main__":
    main()
