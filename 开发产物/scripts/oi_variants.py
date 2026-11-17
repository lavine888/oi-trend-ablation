# SPDX-License-Identifier: GPL-3.0-or-later
"""持仓信息还有没有别的用法？——把「ΔOI 确认门」换成 5 种构造，逐个检验。

动机：主研究证伪的是**一种**用法（`sign(ΔOI_5d)==sign(mom60)` 的确认门）。
审核最常见的反驳是「是不是构造没选对」。这个脚本把同一份持仓数据换 5 种常见用法：

    V1 原始门（参照）     sign(ΔOI_5d) 与动量同向才持仓
    V2 强确认门           同向 **且** |ΔOI_5d| 位于近 250 日的前 50%
    V3 持仓高位门         OI 处于近 250 日高位（≥80% 分位）才持仓，方向随动量
    V4 反向门             只在 ΔOI 与动量**不一致**时持仓（检验符号约定是否写反）
    V5 仅持仓变化         完全不用价格趋势：w = sign(ΔOI_5d)/N
    V6 持仓变化极值       只在 |ΔOI_5d| 位于横截面前 1/3 时，按 sign(ΔOI_5d) 建仓

全部沿用主研究的宇宙、IS/OOS 切分、5 日调仓与 5bp 成本；只改「怎么用 OI」。

运行：
    python 开发产物/scripts/oi_variants.py
产出：
    results/oi_variants.json
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
CST = timezone(timedelta(hours=8))
COST = 0.0005


def load() -> tuple[pd.DataFrame, list[str]]:
    sig = pd.read_parquet(OUT / "signals.parquet")
    sig["date"] = sig["date"].astype(str)
    uni = json.loads((OUT / "universe.json").read_text(encoding="utf-8"))["included"]
    return sig, uni


def prepared(sig: pd.DataFrame, uni: list[str]) -> pd.DataFrame:
    """补齐构造变体需要的滚动特征（只用当日及以前）。"""
    f = sig[sig["symbol"].isin(uni)].sort_values(["symbol", "date"]).copy()
    out = []
    for sym, g in f.groupby("symbol", sort=True):
        g = g.copy()
        g["oi_max250"] = g["oi"].rolling(250, min_periods=120).max()
        g["oi_pct250"] = g["oi"] / g["oi_max250"]
        g["abs_oi_pct250"] = g["oi_chg5"].abs().rolling(250, min_periods=120).rank(pct=True)
        out.append(g)
    return pd.concat(out, ignore_index=True)


def run(w: pd.DataFrame, r: pd.DataFrame, cost: float) -> dict:
    gross = (w.shift(1).fillna(0.0) * r.fillna(0.0)).sum(axis=1)
    turn = (w - w.shift(1).fillna(0.0)).abs().sum(axis=1)
    net = gross - turn * cost
    return {"net": net, "turnover": turn}


def stats(res: dict) -> dict:
    net, turn = res["net"], res["turnover"]
    return {
        "is_sharpe": bt.ann_sharpe(net.loc[ft.IS_START:ft.IS_END]),
        "oos_sharpe": bt.ann_sharpe(net.loc[ft.OOS_START:ft.OOS_END]),
        "oos_ann_return": float(net.loc[ft.OOS_START:ft.OOS_END].mean() * 252),
        "oos_max_dd": bt.max_drawdown(net.loc[ft.OOS_START:ft.OOS_END]),
        "oos_turnover": float(turn.loc[ft.OOS_START:ft.OOS_END].mean() * 252),
    }


def main() -> int:
    sig, uni = load()
    f = prepared(sig, uni)
    n = len(uni)
    slot = 1.0 / n

    ret = f.pivot_table(index="date", columns="symbol", values="r", aggfunc="first").sort_index()
    reb_mask = (f[f["rebalance"]].assign(_x=1.0)
                .pivot_table(index="date", columns="symbol", values="_x", aggfunc="first")
                .reindex(index=ret.index, columns=ret.columns).notna())

    def weights(col: str) -> pd.DataFrame:
        w = f.pivot_table(index="date", columns="symbol", values=col, aggfunc="first").reindex(ret.index)
        return w.where(reb_mask.fillna(False)).ffill().fillna(0.0)

    f["s"] = np.sign(f["mom60"])
    trad = f[["mom60", "oi_chg5"]].notna().all(axis=1)

    def gate(cond: pd.Series) -> pd.Series:
        return np.where(trad, f["s"] * slot * cond.fillna(False).astype(float), 0.0)

    f["w_b0"] = np.where(trad, f["s"] * slot, 0.0)
    f["v1"] = gate(np.sign(f["oi_chg5"]) == f["s"])
    f["v2"] = gate((np.sign(f["oi_chg5"]) == f["s"]) & (f["abs_oi_pct250"] >= 0.5))
    f["v3"] = gate(f["oi_pct250"] >= 0.8)
    f["v4"] = gate((np.sign(f["oi_chg5"]) != f["s"]) & f["oi_chg5"].notna())
    f["v5"] = np.where(trad, np.sign(f["oi_chg5"]) * slot, 0.0)
    cross_ok = f.groupby("date")["abs_oi_pct250"].transform(lambda s: s.rank(pct=True)) >= 2 / 3
    f["v6"] = np.where(trad & cross_ok.fillna(False), np.sign(f["oi_chg5"]) * slot, 0.0)

    variants = {
        "B0_纯趋势": "w_b0",
        "V1_原始确认门": "v1",
        "V2_强确认门": "v2",
        "V3_持仓高位门": "v3",
        "V4_反向门": "v4",
        "V5_仅持仓变化": "v5",
        "V6_持仓变化极值": "v6",
    }
    res = {name: stats(run(weights(col), ret, COST)) for name, col in variants.items()}
    base = res["B0_纯趋势"]["oos_sharpe"]
    for name, s in res.items():
        s["delta_vs_B0"] = s["oos_sharpe"] - base

    out = {
        "generated_at": datetime.now(CST).isoformat(timespec="seconds"),
        "universe_size": n,
        "cost": COST,
        "windows": {"is": [ft.IS_START, ft.IS_END], "oos": [ft.OOS_START, ft.OOS_END]},
        "results": res,
        "any_positive_delta": any(v["delta_vs_B0"] > 0.10 for k, v in res.items() if k != "B0_纯趋势"),
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "oi_variants.json").write_text(json.dumps(out, ensure_ascii=False, indent=2, default=float),
                                              encoding="utf-8")

    print(f"{'构造':<18}{'IS Sharpe':>10}{'OOS Sharpe':>12}{'Δ vs B0':>10}{'OOS 换手':>10}{'最大回撤':>10}")
    for name, s in res.items():
        print(f"{name:<18}{s['is_sharpe']:>10.3f}{s['oos_sharpe']:>12.3f}"
              f"{s['delta_vs_B0']:>10.3f}{s['oos_turnover']:>10.1f}{s['oos_max_dd']:>10.2%}")
    print(f"\n是否存在 Δ>0.10 的构造：{'是' if out['any_positive_delta'] else '否'}")
    print("写出 results/oi_variants.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
