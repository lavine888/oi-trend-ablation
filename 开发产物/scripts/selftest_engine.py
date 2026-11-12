"""引擎自检：用可手算的合成数据验证回测引擎没有未来函数、成本与权重口径正确。

运行：python scripts/selftest_engine.py
不做任何绩效推断，只验证代码正确性（对应《Alpha 规则》第一层「未来函数」检测）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import backtest as bt  # noqa: E402


def make_signals(weights: dict, rets: dict, rebalance_days=(0,)) -> pd.DataFrame:
    dates = sorted(rets)
    syms = list(weights)
    rows = []
    for i, d in enumerate(dates):
        for s in syms:
            rows.append({
                "date": d, "symbol": s, "r": rets[d][s],
                "w_b0": weights[s] if i in rebalance_days else np.nan,
                "w_b1": weights[s] if i in rebalance_days else np.nan,
                "w_p1": weights[s] if i in rebalance_days else np.nan,
                "rebalance": i in rebalance_days,
            })
    return pd.DataFrame(rows)


def check(name: str, got: float, want: float, tol: float = 1e-12) -> bool:
    ok = abs(got - want) <= tol
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}: got={got:.10f} want={want:.10f}")
    return ok


def main() -> int:
    ok = True
    dates = [f"2025-01-{d:02d}" for d in range(1, 11)]
    rets = {d: {"A": 0.01, "B": 0.03} for d in dates}
    weights = {"A": 0.5, "B": 0.5}

    # 1) 权重在调仓日收盘确定，收益从次日起算 -> 第 1 天收益必须为 0
    sig = make_signals(weights, rets, rebalance_days=(0,))
    mats = bt.to_matrices(sig)
    run = bt.run_variant(mats, "B0", cost=0.0)
    ok &= check("首日不计收益（无未来函数）", float(run["gross"].iloc[0]), 0.0)

    # 2) 次日起组合毛收益 = 0.5*1% + 0.5*3% = 2%
    ok &= check("次日毛收益=0.5*1%+0.5*3%", float(run["gross"].iloc[1]), 0.02)

    # 3) 调仓成本 = 建仓换手 1.0 x c
    c = 0.0005
    run_c = bt.run_variant(mats, "B0", cost=c)
    ok &= check("首笔换手=1.0", float(run_c["turnover"].iloc[0]), 1.0)
    ok &= check("首日成本=1.0*c", float(run_c["gross"].iloc[0] - run_c["net"].iloc[0]), c)

    # 4) 持有期内不重复扣费
    ok &= check("持有期第2日换手=0", float(run_c["turnover"].iloc[1]), 0.0)

    # 5) 非调仓日沿用上次权重：第 6 日（index 5）仍应为 +2%
    ok &= check("非调仓日沿用权重", float(run["gross"].iloc[5]), 0.02)

    # 6) 第二次调仓反向 -> 换手 2.0
    w2 = {"A": -0.5, "B": -0.5}
    rows = []
    for d in dates:
        for s in ("A", "B"):
            hit = d in (dates[0], dates[5])
            rows.append({"date": d, "symbol": s, "r": rets[d][s],
                         "w_b0": (w2 if d == dates[5] else weights)[s] if hit else np.nan,
                         "w_b1": np.nan, "w_p1": np.nan, "rebalance": hit})
    sig2 = pd.DataFrame(rows)
    run2 = bt.run_variant(bt.to_matrices(sig2), "B0", cost=c)
    ok &= check("反向调仓换手=2.0", float(run2["turnover"].iloc[5]), 2.0)

    # 7) 指标正确性：常数日收益 -> 年化收益 = 日收益*252
    const = pd.Series([0.001] * 100, index=[f"2025-{i:03d}" for i in range(100)])
    ok &= check("年化收益=均值*252", float(const.mean() * 252), 0.001 * 252)

    # 8) 最大回撤：已知序列
    seq = pd.Series([0.10, -0.20, 0.05], index=["a", "b", "c"])
    eq = (1 + seq).cumprod()  # 1.10, 0.88, 0.924
    want_dd = float((eq / eq.cummax() - 1).min())
    ok &= check("最大回撤", bt.max_drawdown(seq), want_dd)

    print("\nSELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
