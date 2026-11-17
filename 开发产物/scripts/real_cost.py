# SPDX-License-Identifier: GPL-3.0-or-later
"""逐品种费率下的重算：把「统一 5bp」换成「每个品种的手续费 + 跳数滑点」。

费率来源与不确定性见 `references/fee_schedule.csv`：
- 合约乘数取自平台 `get_future_detail`（可核）；
- 最小变动价位与手续费是公开资料整理（标注 confidence），不是平台字段。

因此本脚本不主张「精确成本」，而是给出三档情景：手续费 only / +1 跳 / +2 跳，
再看 ΔSharpe 是否仍然为负——结论只依赖「成本随换手线性放大」这一结构。

运行：
    python 开发产物/scripts/real_cost.py
产出：
    results/cost_real_fees.json
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import factor as ft  # noqa: E402
import backtest as bt  # noqa: E402

ROOT = HERE.parent.parent
OUT = Path(os.getenv("ALPHA61_OUT") or (ROOT / "开发产物" / "output"))
RESULTS = ROOT / "results"
FEES = ROOT / "references" / "fee_schedule.csv"
CST = timezone(timedelta(hours=8))
SCENARIOS = {"fees_only": 0, "fees_plus_1tick": 1, "fees_plus_2ticks": 2}


def per_symbol_bps(fees: pd.DataFrame, avg_price: pd.Series, ticks: int) -> pd.Series:
    out = {}
    for _, r in fees.iterrows():
        sym, px = r["symbol"], float(avg_price.get(r["symbol"], np.nan))
        if not np.isfinite(px) or px <= 0:
            continue
        mult = float(r["contract_multiplier"])
        if r["fee_mode"] == "rate_bp":
            commission_bps = float(r["fee_value"])
        else:
            commission_bps = float(r["fee_value"]) / (px * mult) * 1e4
        slip_bps = ticks * float(r["tick_size"]) / px * 1e4
        out[sym] = commission_bps + slip_bps
    return pd.Series(out, dtype="float64")


def main() -> int:
    sig = pd.read_parquet(OUT / "signals.parquet")
    sig["date"] = sig["date"].astype(str)
    fees = pd.read_csv(FEES)
    fees = fees[fees["symbol"].isin(sig["symbol"].unique())]
    mats = bt.to_matrices(sig)

    # 用全样本 settlement 中位数作为「名义价格」，把按手/按跳的成本换算成 bp
    avg_price = sig.groupby("symbol")["settlement"].median()

    result = {"generated_at": datetime.now(CST).isoformat(timespec="seconds"),
              "fee_source": "references/fee_schedule.csv",
              "price_basis": "全样本 settlement 中位数",
              "scenarios": {}}

    for name, ticks in SCENARIOS.items():
        vec = per_symbol_bps(fees, avg_price, ticks)
        r0 = bt.run_variant(mats, "B0", 0.0005, cost_bps=vec)
        r1 = bt.run_variant(mats, "B1", 0.0005, cost_bps=vec)
        s0 = bt.ann_sharpe(r0["net"].loc[ft.OOS_START:ft.OOS_END])
        s1 = bt.ann_sharpe(r1["net"].loc[ft.OOS_START:ft.OOS_END])
        result["scenarios"][name] = {
            "ticks_slippage": ticks,
            "cost_bps_median": float(vec.median()),
            "cost_bps_min": float(vec.min()),
            "cost_bps_max": float(vec.max()),
            "oos_sharpe_B0": s0,
            "oos_sharpe_B1": s1,
            "delta": s1 - s0,
            "oos_turnover_B0": float(r0["turnover"].loc[ft.OOS_START:ft.OOS_END].mean() * 252),
            "oos_turnover_B1": float(r1["turnover"].loc[ft.OOS_START:ft.OOS_END].mean() * 252),
        }

    # 对照：统一 5bp
    u0 = bt.run_variant(mats, "B0", 0.0005)
    u1 = bt.run_variant(mats, "B1", 0.0005)
    result["uniform_5bp"] = {
        "oos_sharpe_B0": bt.ann_sharpe(u0["net"].loc[ft.OOS_START:ft.OOS_END]),
        "oos_sharpe_B1": bt.ann_sharpe(u1["net"].loc[ft.OOS_START:ft.OOS_END]),
        "delta": (bt.ann_sharpe(u1["net"].loc[ft.OOS_START:ft.OOS_END])
                  - bt.ann_sharpe(u0["net"].loc[ft.OOS_START:ft.OOS_END])),
    }

    print(f"逐品种成本（bp/单边）分位：中位 {result['scenarios']['fees_plus_1tick']['cost_bps_median']:.2f}，"
          f"区间 {result['scenarios']['fees_plus_1tick']['cost_bps_min']:.2f} ~ "
          f"{result['scenarios']['fees_plus_1tick']['cost_bps_max']:.2f}")
    print(f"{'情景':<20}{'B0':>9}{'B1':>9}{'Δ':>9}{'换手B0':>9}{'换手B1':>9}")
    for name, s in result["scenarios"].items():
        print(f"{name:<20}{s['oos_sharpe_B0']:>9.3f}{s['oos_sharpe_B1']:>9.3f}{s['delta']:>9.3f}"
              f"{s['oos_turnover_B0']:>9.1f}{s['oos_turnover_B1']:>9.1f}")
    u = result["uniform_5bp"]
    print(f"{'统一 5bp（对照）':<20}{u['oos_sharpe_B0']:>9.3f}{u['oos_sharpe_B1']:>9.3f}{u['delta']:>9.3f}")

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "cost_real_fees.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    print("\n写出 results/cost_real_fees.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
