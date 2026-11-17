# SPDX-License-Identifier: GPL-3.0-or-later
"""复现检验：把同一套参数放到 2010–2015 的商品期货上（时间维度独立样本）。

口径见 `00_预注册_复现_2010-2015.md`。与主研究的差别只有数据窗口与 IS/OOS 切分，
信号、门、权重、调仓、成本全部一致。

运行：
    python 开发产物/scripts/replicate_2010_2015.py
产出：
    results/replication_2010_2015.json
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

# 复现窗口（与主研究不同，事先冻结）
R_IS_START, R_IS_END = "2010-01-01", "2012-12-31"
R_OOS_START, R_OOS_END = "2013-01-01", "2015-12-31"
CHUNKS = [("20100101", "20121231"), ("20130101", "20151231")]
COSTS = {"2bp": 0.0002, "5bp": 0.0005, "10bp": 0.0010}


def fetch(symbols):
    frames = []
    for start, end in CHUNKS:
        try:
            df = pd_client.future_daily(symbols, start, end)
            if len(df):
                frames.append(df)
                print(f"  [{start}-{end}] 批量 {len(df)} 行")
                continue
        except Exception as exc:  # noqa: BLE001
            print(f"  [{start}-{end}] 批量失败（{type(exc).__name__}），逐品种回退")
        for i, sym in enumerate(symbols, 1):
            try:
                df = pd_client.future_daily([sym], start, end)
                if len(df):
                    frames.append(df)
            except Exception:  # noqa: BLE001
                pass
            if i % 10 == 0:
                print(f"    进度 {i}/{len(symbols)}")
    if not frames:
        raise SystemExit("2010–2015 未返回数据")
    return (pd.concat(frames, ignore_index=True)
            .drop_duplicates(subset=["symbol", "date"])
            .sort_values(["symbol", "date"]).reset_index(drop=True))


def select_universe_is(feat: pd.DataFrame, is_start: str, is_end: str):
    """复现窗口专用：只用给定的 IS 段判定宇宙，规则与主研究一致。"""
    dates_is = feat.loc[(feat.date >= is_start) & (feat.date <= is_end), "date"].nunique()
    keep, audit = [], []
    for sym, g in feat.groupby("symbol", sort=True):
        gi = g[(g.date >= is_start) & (g.date <= is_end)]
        valid = int(g["r"].notna().sum())
        cov = (gi["date"].nunique() / dates_is) if dates_is else 0.0
        amt = float(gi.loc[gi["amt"].notna(), "amt"].median()) if gi["amt"].notna().any() else 0.0
        reasons = []
        if valid < ft.MIN_ROWS_FULL:
            reasons.append(f"valid_rows<{ft.MIN_ROWS_FULL}({valid})")
        if amt < ft.MIN_AMOUNT_IS:
            reasons.append(f"is_amount_median<{ft.MIN_AMOUNT_IS:.0e}")
        if cov < ft.MIN_COVERAGE_IS:
            reasons.append(f"is_coverage<{ft.MIN_COVERAGE_IS}({cov:.3f})")
        audit.append({"symbol": sym, "included": not reasons, "valid_rows": valid,
                      "is_coverage": round(cov, 4), "is_amount_median": amt, "reasons": reasons})
        if not reasons:
            keep.append(sym)
    return keep, audit


def run_split(feat: pd.DataFrame, is_start: str, is_end: str,
              oos_start: str, oos_end: str) -> dict:
    """按给定切分重跑一遍：IS 定宇宙 → 出信号 → 扣费回测。"""
    universe, _ = select_universe_is(feat, is_start, is_end)
    sig = ft.build_signals(feat, universe)
    sig = sig[(sig["date"] >= is_start) & (sig["date"] <= oos_end)].copy()
    sig["rebalance"] = False
    for sym in universe:
        idx = sig.index[sig["symbol"] == sym]
        if len(idx):
            sig.loc[idx[:: ft.REBALANCE_DAYS], "rebalance"] = True
    mats = bt.to_matrices(sig)

    blk = {"universe_size": len(universe), "rows": int(len(sig))}
    for label, c in COSTS.items():
        runs = {k: bt.run_variant(mats, k, c) for k in ("B0", "B1", "P1")}
        sub = {}
        for k, v in runs.items():
            net = v["net"]
            sub[k] = {
                "is_sharpe": bt.ann_sharpe(net.loc[is_start:is_end]),
                "oos_sharpe": bt.ann_sharpe(net.loc[oos_start:oos_end]),
                "oos_ann_return": float(net.loc[oos_start:oos_end].mean() * 252),
                "oos_max_dd": bt.max_drawdown(net.loc[oos_start:oos_end]),
                "oos_turnover": float(v["turnover"].loc[oos_start:oos_end].mean() * 252),
            }
        sub["delta_b1_minus_b0"] = sub["B1"]["oos_sharpe"] - sub["B0"]["oos_sharpe"]
        blk[label] = sub

    # 逐年 Δ（用 5bp 档）
    net0 = runs_net(mats, "B0", COSTS["5bp"])
    net1 = runs_net(mats, "B1", COSTS["5bp"])
    years = sorted({d[:4] for d in net0.loc[oos_start:oos_end].index})
    blk["yearly_delta"] = {
        y: (bt.ann_sharpe(net1.loc[f"{y}-01-01":f"{y}-12-31"])
            - bt.ann_sharpe(net0.loc[f"{y}-01-01":f"{y}-12-31"]))
        for y in years
    }
    return blk


def runs_net(mats, key: str, cost: float) -> pd.Series:
    return bt.run_variant(mats, key, cost)["net"]


def main() -> int:
    pd_client.bootstrap()
    symbols = [ft.symbol_of(v, e) for v, e in ft.CANDIDATES]
    print(f"拉取 {len(symbols)} 个候选主力连续序列（2010–2015）")
    raw = fetch(symbols)
    print(f"合计 {len(raw)} 行，{raw['date'].min()} ~ {raw['date'].max()}")

    feat = ft.build_features(raw)

    splits = {
        "S0_原冻结": (R_IS_START, R_IS_END, R_OOS_START, R_OOS_END),
        "S1_更早切": ("2010-01-01", "2011-12-31", "2012-01-01", "2015-12-31"),
        "S2_更晚切": ("2010-01-01", "2013-12-31", "2014-01-01", "2015-12-31"),
    }
    out_splits = {name: run_split(feat, *w) for name, w in splits.items()}

    s0 = out_splits["S0_原冻结"]
    result = {
        "generated_at": datetime.now(CST).isoformat(timespec="seconds"),
        "spec": "00_预注册_复现_2010-2015.md",
        "addendum": "00_预注册_复现_2010-2015_补充说明.md",
        "universe": None,   # 由 run_split 内部决定，见 splits
        "rows": s0["rows"],
        "is_window": [R_IS_START, R_IS_END],
        "oos_window": [R_OOS_START, R_OOS_END],
        "costs": {label: s0[label] for label in COSTS},
        "splits": out_splits,
    }

    # 兼容既有检查：从 S0 反推品种数
    result["universe_size"] = s0["universe_size"]

    p = result["costs"]["5bp"]
    print("\n2010–2015 复现（主口径 S0，扣费 5bp）")
    print(f"  品种 {s0['universe_size']} 个")
    print(f"  IS  Sharpe: B0 {p['B0']['is_sharpe']:+.3f}  B1 {p['B1']['is_sharpe']:+.3f}")
    print(f"  OOS Sharpe: B0 {p['B0']['oos_sharpe']:+.3f}  B1 {p['B1']['oos_sharpe']:+.3f}  "
          f"P1 {p['P1']['oos_sharpe']:+.3f}")
    print(f"  Δ(B1−B0) OOS: {p['delta_b1_minus_b0']:+.3f}")

    print("\n切分敏感性（5bp 的 ΔSharpe）")
    for name, blk in out_splits.items():
        print(f"  {name:<12} 品种 {blk['universe_size']:>2d}  Δ={blk['5bp']['delta_b1_minus_b0']:+.3f}")

    print("\nS0 逐年 ΔSharpe")
    for y, d in s0["yearly_delta"].items():
        print(f"  {y}: {d:+.3f}")

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "replication_2010_2015.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    print("\n写出 results/replication_2010_2015.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
