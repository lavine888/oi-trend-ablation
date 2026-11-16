# SPDX-License-Identifier: GPL-3.0-or-later
"""Alpha-61 回测脚本：冻结基线 B0 vs 处理组 B1(+持仓门) vs 安慰剂 P1(+成交门)。

运行
----
    python scripts/backtest.py

产出（开发产物/output/）
----
    bt_metrics.json        各变体 IS/OOS 扣费指标、逐年分解、成本敏感性
    bt_equity.csv          逐日扣费净值
    bt_positions.csv       逐日权重（B0/B1/P1）
    bt_conditional.csv     门通过/不通过两态的条件收益（信号极值证据）

口径：全部来自预注册文件；本脚本不含任何可调阈值。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

ROOT = HERE.parent
OUT = Path(os.getenv("ALPHA61_OUT") or (ROOT / "output"))

import factor as ft  # 窗口参数单一来源，避免与 factor.py 漂移

IS_START, IS_END = ft.IS_START, ft.IS_END
OOS_START, OOS_END = ft.OOS_START, ft.OOS_END
VARIANTS = {"B0": "w_b0", "B1": "w_b1", "P1": "w_p1"}
COST_GRID = [0.0002, 0.0005, 0.0010]
PRIMARY_COST = 0.0005


# ---------------------------------------------------------------- engine
def to_matrices(sig: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """把长表拆成 日期×品种 的收益矩阵与目标权重矩阵（按调仓日生效）。"""
    ret = sig.pivot_table(index="date", columns="symbol", values="r", aggfunc="first").sort_index()
    reb = (sig[sig["rebalance"]].assign(_rb=1.0)
           .pivot_table(index="date", columns="symbol", values="_rb", aggfunc="first")
           .reindex(index=ret.index, columns=ret.columns).notna())
    mats: Dict[str, pd.DataFrame] = {"ret": ret, "rebalance": reb.reindex(ret.index).fillna(False)}
    for name, col in VARIANTS.items():
        w = sig.pivot_table(index="date", columns="symbol", values=col, aggfunc="first").sort_index()
        w = w.reindex(ret.index)
        # 非调仓日沿用上一次调仓目标
        w = w.where(reb.reindex(ret.index).fillna(False)).ffill().fillna(0.0)
        mats[name] = w
    return mats


def run_variant(mats: Dict[str, pd.DataFrame], col: str, cost: float) -> pd.DataFrame:
    """返回逐日 gross/net 收益与换手。权重 t 日收盘确定，t+1 计收益。"""
    w = mats[col].fillna(0.0)
    r = mats["ret"].fillna(0.0)
    gross = (w.shift(1).fillna(0.0) * r).sum(axis=1)
    turnover = (w - w.shift(1).fillna(0.0)).abs().sum(axis=1)
    net = gross - turnover * cost
    return pd.DataFrame({"gross": gross, "turnover": turnover, "net": net,
                         "gross_exposure": w.abs().sum(axis=1).shift(1).fillna(0.0)})


# ---------------------------------------------------------------- metrics
def ann_sharpe(x: pd.Series) -> float:
    x = x.dropna()
    if len(x) < 30 or x.std() == 0:
        return float("nan")
    return float(x.mean() / x.std() * np.sqrt(252))


def max_drawdown(x: pd.Series) -> float:
    eq = (1 + x.fillna(0)).cumprod()
    peak = eq.cummax()
    return float((eq / peak - 1).min())


def summarize(net: pd.Series, turnover: pd.Series, label: str) -> dict:
    net = net.dropna()
    yr = net.groupby(net.index.str[:4])
    return {
        "variant": label,
        "days": int(len(net)),
        "ann_return": float(net.mean() * 252),
        "ann_vol": float(net.std() * np.sqrt(252)),
        "sharpe": ann_sharpe(net),
        "max_dd": max_drawdown(net),
        "turnover_ann": float(turnover.mean() * 252),
        "hit_rate": float((net > 0).mean()),
        "cvar95": float(net[net <= net.quantile(0.05)].mean()) if len(net) else float("nan"),
        "by_year": {y: {"sharpe": ann_sharpe(g), "ret": float(g.sum())} for y, g in yr},
    }


def window(net: pd.Series, start: str, end: str) -> pd.Series:
    return net.loc[(net.index >= start) & (net.index <= end)]


# ---------------------------------------------------------------- IC / 分层
def forward_returns(sig: pd.DataFrame, horizon: int = 5) -> pd.Series:
    """逐品种 t+1..t+horizon 的累计收益，与 sig 同索引对齐。"""
    out = []
    for sym, g in sig.groupby("symbol", sort=True):
        g = g.sort_values("date")
        r = g["r"].shift(-1).rolling(horizon).sum().shift(-(horizon - 1))
        out.append(pd.Series(r.values, index=g.index))
    return pd.concat(out).sort_index()


def ic_and_layers(sig: pd.DataFrame, horizon: int = 5, n_layers: int = 5) -> tuple:
    """调仓日的横截面 IC（Spearman）与分层前向收益。

    因子列：
      mom60     纯价格趋势（基线因子）
      gated     带门趋势 = mom60 × 1[持仓确认]（处理组因子）
      gate_oi   门本身（0/1），检验「确认」是否单独含预测力
    """
    s = sig.copy()
    s["fwd"] = forward_returns(s, horizon)
    reb = s[s["rebalance"] & s["tradable"] & s["fwd"].notna()].copy()
    reb["gated"] = reb["mom60"] * (reb["gate_oi"] == 1)

    rows = []
    for d, g in reb.groupby("date"):
        if len(g) < max(8, n_layers * 2):
            continue
        rows.append({
            "date": d, "n": len(g),
            "ic_mom": g["mom60"].corr(g["fwd"], method="spearman"),
            "ic_gated": g["gated"].corr(g["fwd"], method="spearman"),
            "ic_gate": g["gate_oi"].corr(g["fwd"], method="spearman"),
        })
    ic = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)

    def summ(col: str) -> dict:
        x = ic[col].dropna()
        if x.empty:
            return {"n": 0, "mean_ic": float("nan"), "icir": float("nan"), "ic_win_rate": float("nan")}
        ir = float(x.mean() / x.std()) if x.std() else float("nan")
        return {"n": int(len(x)), "mean_ic": float(x.mean()), "ic_std": float(x.std()),
                "icir": ir, "ic_win_rate": float((x > 0).mean())}

    # 分层：按 mom60 分 5 层（用分位排名切层，避免 qcut 在标的少/并列时炸裂）
    def to_layer(v: pd.Series) -> pd.Series:
        r = v.rank(method="first", pct=True)
        return np.ceil(r * n_layers).clip(1, n_layers).astype(int)

    reb["layer"] = reb.groupby("date")["mom60"].transform(to_layer)
    layers = (reb.groupby("layer")["fwd"].agg(["mean", "median", "count"]).reset_index())
    return ic, {"mom60": summ("ic_mom"), "gated": summ("ic_gated"), "gate_oi": summ("ic_gate")}, layers


def _ic_window(ic: pd.DataFrame, col: str, start: str, end: str) -> dict:
    x = ic.loc[(ic["date"] >= start) & (ic["date"] <= end), col].dropna()
    if x.empty:
        return {"n": 0, "mean_ic": float("nan"), "icir": float("nan"), "ic_win_rate": float("nan")}
    return {"n": int(len(x)), "mean_ic": float(x.mean()),
            "icir": float(x.mean() / x.std()) if x.std() else float("nan"),
            "ic_win_rate": float((x > 0).mean())}


# ---------------------------------------------------------------- main
def main() -> None:
    sig = pd.read_parquet(OUT / "signals.parquet")
    sig["date"] = sig["date"].astype(str)
    mats = to_matrices(sig)

    runs = {k: run_variant(mats, k, PRIMARY_COST) for k in VARIANTS}

    equity = pd.DataFrame({k: (1 + v["net"]).cumprod() for k, v in runs.items()})
    equity.index.name = "date"
    equity.to_csv(OUT / "bt_equity.csv", encoding="utf-8-sig")

    pos = pd.concat({k: mats[k] for k in VARIANTS}, axis=1)
    pos.to_csv(OUT / "bt_positions.csv", encoding="utf-8-sig")

    result: Dict[str, object] = {"primary_cost_bps": PRIMARY_COST * 1e4, "windows": {}}
    for wname, (s, e) in {"IS": (IS_START, IS_END), "OOS": (OOS_START, OOS_END), "FULL": ("0000", "9999")}.items():
        block = {}
        for k, v in runs.items():
            block[k] = summarize(window(v["net"], s, e), window(v["turnover"], s, e), k)
        block["delta_oos"] = None
        if wname == "OOS":
            block["delta"] = {
                "sharpe_B1_minus_B0": block["B1"]["sharpe"] - block["B0"]["sharpe"],
                "sharpe_P1_minus_B0": block["P1"]["sharpe"] - block["B0"]["sharpe"],
                "maxdd_B1_minus_B0": block["B1"]["max_dd"] - block["B0"]["max_dd"],
                "ret_B1_minus_B0": block["B1"]["ann_return"] - block["B0"]["ann_return"],
                "sharpe_by_year_delta": {
                    y: (block["B1"]["by_year"].get(y, {}).get("sharpe", float("nan"))
                        - block["B0"]["by_year"].get(y, {}).get("sharpe", float("nan")))
                    for y in sorted(block["B0"]["by_year"])
                },
            }
        result["windows"][wname] = block

    # 成本敏感性 + 盈亏平衡成本（OOS）
    sens = {}
    for c in COST_GRID:
        r0 = run_variant(mats, "B0", c)
        r1 = run_variant(mats, "B1", c)
        s0 = ann_sharpe(window(r0["net"], OOS_START, OOS_END))
        s1 = ann_sharpe(window(r1["net"], OOS_START, OOS_END))
        sens[f"{c*1e4:.0f}bp"] = {"sharpe_B0": s0, "sharpe_B1": s1, "delta": s1 - s0}
    lo, hi = 0.0, 0.02
    for _ in range(60):
        mid = (lo + hi) / 2
        d = (ann_sharpe(window(run_variant(mats, "B1", mid)["net"], OOS_START, OOS_END))
             - ann_sharpe(window(run_variant(mats, "B0", mid)["net"], OOS_START, OOS_END)))
        if d > 0:
            lo = mid
        else:
            hi = mid
    result["cost_sensitivity_oos"] = sens
    result["breakeven_cost_bps"] = lo * 1e4
    result["note_breakeven"] = "使 ΔSharpe(OOS) 归零的单边成本；>= 该值时 B1 不再优于 B0"

    # 条件收益（信号极值证据）：门通过 vs 不通过时，下一期（5 日）该品种的收益
    cond = conditional_returns(sig)
    cond.to_csv(OUT / "bt_conditional.csv", index=False, encoding="utf-8-sig")
    result["conditional"] = summarize_conditional(cond)

    # IC / ICIR / 分层（《Alpha 规则》第 6 节必备指标）
    ic, ic_summary, layers = ic_and_layers(sig)
    ic.to_csv(OUT / "bt_ic.csv", index=False, encoding="utf-8-sig")
    layers.to_csv(OUT / "bt_layers.csv", index=False, encoding="utf-8-sig")
    result["ic"] = {
        "horizon_days": 5,
        "definition": "调仓日横截面 Spearman；gated = mom60 × 1[持仓确认]",
        "summary": ic_summary,
        "oos_summary": {
            k: _ic_window(ic, k, OOS_START, OOS_END) for k in ("ic_mom", "ic_gated", "ic_gate")
        },
    }
    result["layers"] = layers.to_dict(orient="records")
    result["layer_monotonicity"] = float(layers["mean"].iloc[-1] - layers["mean"].iloc[0]) if len(layers) >= 2 else None

    (OUT / "bt_metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=float),
                                         encoding="utf-8")
    print(json.dumps({w: {k: round(v["sharpe"], 3) if isinstance(v.get("sharpe"), float) else v.get("sharpe")
                          for k, v in b.items() if k in ("B0", "B1", "P1")}
                      for w, b in result["windows"].items()}, ensure_ascii=False, indent=2))
    print("delta(OOS):", {k: round(v, 4) for k, v in result["windows"]["OOS"]["delta"].items()
                          if isinstance(v, float)})
    print("breakeven_cost_bps:", round(result["breakeven_cost_bps"], 2))


def conditional_returns(sig: pd.DataFrame) -> pd.DataFrame:
    """逐品种、逐调仓日：门状态 → 未来 5 日该品种收益（用于极值案例与条件均值）。"""
    rows: List[dict] = []
    for sym, g in sig.groupby("symbol", sort=True):
        g = g.sort_values("date")
        fwd = g["r"].shift(-1).rolling(5).sum().shift(-4)  # t+1..t+5
        sub = g[g["rebalance"]].copy()
        sub["fwd5"] = fwd.reindex(sub.index)
        rows.append(sub[["date", "symbol", "s", "gate_oi", "gate_vol", "oi_chg5", "mom60", "fwd5", "tradable"]])
    return pd.concat(rows, ignore_index=True).dropna(subset=["fwd5"])


def summarize_conditional(cond: pd.DataFrame) -> dict:
    t = cond[cond["tradable"]]
    out = {}
    for name, col in (("gate_oi", "gate_oi"), ("gate_vol", "gate_vol")):
        on = t[t[col] == 1]["fwd5"]
        off = t[t[col] == 0]["fwd5"]
        out[name] = {
            "n_on": int(len(on)), "n_off": int(len(off)),
            "mean_on": float(on.mean()), "mean_off": float(off.mean()),
            "hit_on": float((on > 0).mean()), "hit_off": float((off > 0).mean()),
            "t_stat": float((on.mean() - off.mean()) / np.sqrt(on.var() / len(on) + off.var() / len(off)))
            if len(on) > 2 and len(off) > 2 else float("nan"),
        }
    return out


if __name__ == "__main__":
    main()
