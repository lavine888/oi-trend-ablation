# SPDX-License-Identifier: GPL-3.0-or-later
"""外部复现：把 Alpha-61 的同一套参数搬到 CFFEX（股指 + 国债期货），看负结果是否复现。

这不是重新调参，也不是新假设。它回答一个问题：
    在完全不同的资产类别上，用**完全相同**的 60 日动量 + 持仓确认门 + 5bp 成本，
    ΔSharpe 是否仍然是零附近或为负？

口径冻结（与主研究一致）：
- 收益 `settlement / pre_settlement − 1`；动量 60 日；持仓变化 5 日；5 日调仓
- IS 2016–2021（只用于宇宙过滤，不做参数选择）；OOS 2022–2025
- 成本 5bp/单边，另报 2bp / 10bp

运行：
    python 开发产物/scripts/replicate_cffex.py
产出：
    results/replication_cffex.json
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pd_client  # noqa: E402
import factor as ft  # noqa: E402
import backtest as bt  # noqa: E402

ROOT = HERE.parent.parent
RESULTS = ROOT / "results"
CST = timezone(timedelta(hours=8))

# CFFEX 主力连续序列（后缀是 .CFE，实测可用）
VARIETIES = ["IF", "IH", "IC", "IM", "T", "TF", "TS", "TL"]
SYMBOLS = [f"{v}_DOMINANT.CFE" for v in VARIETIES]
CHUNKS = [("20160101", "20201231"), ("20210101", "20251231")]
COSTS = {"2bp": 0.0002, "5bp": 0.0005, "10bp": 0.0010}


def fetch() -> pd.DataFrame:
    frames = []
    for start, end in CHUNKS:
        df = pd_client.future_daily(SYMBOLS, start, end)
        if len(df):
            frames.append(df)
            print(f"  [{start}-{end}] {len(df)} 行")
    if not frames:
        raise SystemExit("CFFEX 未返回数据")
    return (pd.concat(frames, ignore_index=True)
            .drop_duplicates(subset=["symbol", "date"])
            .sort_values(["symbol", "date"]).reset_index(drop=True))


def main() -> int:
    pd_client.bootstrap()
    print(f"拉取 {len(SYMBOLS)} 个 CFFEX 主力连续序列：{', '.join(VARIETIES)}")
    raw = fetch()
    print(f"合计 {len(raw)} 行，{raw['date'].min()} ~ {raw['date'].max()}")

    feat = ft.build_features(raw)
    # 与主研究同一套宇宙规则（只用 IS 判定）
    universe, audit = ft.select_universe(feat)
    print(f"通过 IS 规则的品种：{len(universe)} → {', '.join(u.split('_')[0] for u in universe)}")
    for row in audit:
        if not row["included"]:
            print(f"  剔除 {row['symbol']}: {row['reasons']}")
    if len(universe) < 4:
        raise SystemExit("可用品种太少，复现无意义")

    sig = ft.build_signals(feat, universe)
    sig["rebalance"] = False
    for sym in universe:
        idx = sig.index[sig["symbol"] == sym]
        if len(idx):
            sig.loc[idx[:: ft.REBALANCE_DAYS], "rebalance"] = True

    mats = bt.to_matrices(sig)
    out = {"generated_at": datetime.now(CST).isoformat(timespec="seconds"),
           "universe": universe, "rows": int(len(sig)),
           "date_min": str(sig["date"].min()), "date_max": str(sig["date"].max()),
           "costs": {}}

    for label, c in COSTS.items():
        r = {k: bt.run_variant(mats, k, c) for k in ("B0", "B1", "P1")}
        blk = {}
        for k in ("B0", "B1", "P1"):
            net = r[k]["net"]
            blk[k] = {
                "is_sharpe": bt.ann_sharpe(net.loc[ft.IS_START:ft.IS_END]),
                "oos_sharpe": bt.ann_sharpe(net.loc[ft.OOS_START:ft.OOS_END]),
                "oos_ann_return": float(net.loc[ft.OOS_START:ft.OOS_END].mean() * 252),
                "oos_ann_vol": float(net.loc[ft.OOS_START:ft.OOS_END].std() * np.sqrt(252)),
                "oos_max_dd": bt.max_drawdown(net.loc[ft.OOS_START:ft.OOS_END]),
                "oos_turnover": float(r[k]["turnover"].loc[ft.OOS_START:ft.OOS_END].mean() * 252),
            }
        blk["delta_b1_minus_b0"] = blk["B1"]["oos_sharpe"] - blk["B0"]["oos_sharpe"]
        blk["delta_p1_minus_b0"] = blk["P1"]["oos_sharpe"] - blk["B0"]["oos_sharpe"]
        out["costs"][label] = blk

    primary = out["costs"]["5bp"]
    print("\n外部复现结果（CFFEX，扣费 5bp）")
    print(f"  IS  Sharpe: B0 {primary['B0']['is_sharpe']:+.3f}  B1 {primary['B1']['is_sharpe']:+.3f}")
    print(f"  OOS Sharpe: B0 {primary['B0']['oos_sharpe']:+.3f}  B1 {primary['B1']['oos_sharpe']:+.3f}  "
          f"P1 {primary['P1']['oos_sharpe']:+.3f}")
    print(f"  Δ(B1−B0) OOS: {primary['delta_b1_minus_b0']:+.3f}   Δ(P1−B0): {primary['delta_p1_minus_b0']:+.3f}")
    print(f"  OOS 换手: B0 {primary['B0']['oos_turnover']:.1f} → B1 {primary['B1']['oos_turnover']:.1f}")

    RESULTS.mkdir(exist_ok=True)
    target = RESULTS / "replication_cffex.json"
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    print(f"\n写出 {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
