# SPDX-License-Identifier: GPL-3.0-or-later
"""Alpha-61 验证脚本：未来函数、参数邻域、置换检验、波动匹配、极值案例、风险识别。

运行
----
    python scripts/validate.py

产出（开发产物/output/）
----
    validate_report.json   三层沙漏检测结论 + 稳健性 + 风险识别
    extreme_cases.csv      信号极值案例清单（可追溯到品种/日期/字段值）
    permutation.json       置换检验零分布摘要

设计原则：本脚本只做「检验」与「披露」，不参与任何选择；主结论仍以预注册的
LOOKBACK=60 / OI_LOOKBACK=5 / c=5bp 为准。
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
import backtest as bt  # noqa: E402
import factor as ft  # noqa: E402

ROOT = HERE.parent
OUT = Path(os.getenv("ALPHA61_OUT") or (ROOT / "output"))
RNG = np.random.default_rng(20261103)

IS_START, IS_END = ft.IS_START, ft.IS_END
OOS_START, OOS_END = ft.OOS_START, ft.OOS_END


# ------------------------------------------------------------ 1. 未来函数
def causal_check(sig: pd.DataFrame, n_samples: int = 40) -> dict:
    """抽样重算：只用 t 日及以前的数据，复现 t 日的 mom60 / oi_chg5。

    若代码里存在任何未来信息（例如用了 t+1 的收盘价或在全样本上做了标准化），
    重算值会与落盘值不一致。
    """
    rng = np.random.default_rng(7)
    rows = sig[sig["mom60"].notna() & sig["oi_chg5"].notna()]
    idx = rng.choice(len(rows), size=min(n_samples, len(rows)), replace=False)
    mism = []
    for i in idx:
        rec = rows.iloc[i]
        g = sig[sig["symbol"] == rec["symbol"]].sort_values("date")
        g = g[g["date"] <= rec["date"]]
        if len(g) < ft.LOOKBACK:
            continue
        lr = np.log1p(g["r"].clip(lower=-0.99))
        mom = float(lr.tail(ft.LOOKBACK).sum())
        oi_now = g["oi"].iloc[-1]
        oi_prev = g["oi"].iloc[-1 - ft.OI_LOOKBACK] if len(g) > ft.OI_LOOKBACK else np.nan
        oi_chg = float(oi_now / oi_prev - 1.0) if pd.notna(oi_prev) and oi_prev else np.nan
        ok_mom = abs(mom - rec["mom60"]) < 1e-9
        ok_oi = (pd.isna(oi_chg) and pd.isna(rec["oi_chg5"])) or abs(oi_chg - rec["oi_chg5"]) < 1e-9
        if not (ok_mom and ok_oi):
            mism.append({"symbol": rec["symbol"], "date": rec["date"],
                         "mom_stored": float(rec["mom60"]), "mom_recalc": mom,
                         "oi_stored": float(rec["oi_chg5"]), "oi_recalc": oi_chg})
    return {"samples": int(min(n_samples, len(rows))), "mismatches": len(mism), "detail": mism[:10],
            "passed": len(mism) == 0}


def lag_injection_check(sig: pd.DataFrame) -> dict:
    """负向对照：把收益整体后移 1 日（人为引入未来信息），绩效应显著虚高。

    若「偷看一天」不能改善结果，说明引擎没有正确使用收益序列。
    """
    sig2 = sig.copy()
    for sym, g in sig2.groupby("symbol"):
        r = g.sort_values("date")["r"].shift(-1)  # 未来一日收益
        sig2.loc[g.index, "r"] = r.values
    mats = bt.to_matrices(sig2.dropna(subset=["r"]))
    run = bt.run_variant(mats, "B0", 0.0005)
    net = run["net"].loc[(run.index >= OOS_START) & (run.index <= OOS_END)]
    return {"oos_sharpe_with_lookahead": bt.ann_sharpe(net)}


# ------------------------------------------------------------ 2. 参数邻域
def parameter_neighborhood(df: pd.DataFrame, universe: List[str]) -> List[dict]:
    out = []
    for lb in (20, 40, 60, 120):
        for oi_lb in (3, 5, 10):
            f = df[df["symbol"].isin(universe)].copy()
            rows = []
            for sym, g in f.groupby("symbol", sort=True):
                g = g.sort_values("date").copy()
                lr = np.log1p(g["r"].clip(lower=-0.99))
                g["mom"] = lr.rolling(lb, min_periods=lb).sum()
                g["oi_chg"] = g["oi"] / g["oi"].shift(oi_lb) - 1.0
                rows.append(g)
            f = pd.concat(rows, ignore_index=True)
            n = len(universe)
            slot = 1.0 / n
            s = np.sign(f["mom"])
            tradable = f[["mom", "oi_chg"]].notna().all(axis=1)
            f["w_b0"] = np.where(tradable, s * slot, 0.0)
            f["w_b1"] = np.where(tradable, s * slot * (np.sign(f["oi_chg"]) == s).astype(float), 0.0)
            f["w_p1"] = 0.0
            f["rebalance"] = False
            for sym in universe:
                idx = f.index[f["symbol"] == sym]
                f.loc[idx[::ft.REBALANCE_DAYS], "rebalance"] = True
            mats = bt.to_matrices(f)
            res = {}
            for k in ("B0", "B1"):
                net = bt.run_variant(mats, k, 0.0005)["net"]
                res[k] = {
                    "is": bt.ann_sharpe(net.loc[(net.index >= IS_START) & (net.index <= IS_END)]),
                    "oos": bt.ann_sharpe(net.loc[(net.index >= OOS_START) & (net.index <= OOS_END)]),
                }
            out.append({"lookback": lb, "oi_lookback": oi_lb,
                        "is_sharpe_B0": res["B0"]["is"], "is_sharpe_B1": res["B1"]["is"],
                        "oos_sharpe_B0": res["B0"]["oos"], "oos_sharpe_B1": res["B1"]["oos"],
                        "delta_oos": res["B1"]["oos"] - res["B0"]["oos"]})
    return out


# ------------------------------------------------------------ 3. 置换检验
def permutation_test(sig: pd.DataFrame, n_draws: int = 500) -> dict:
    base = bt.run_variant(bt.to_matrices(sig), "B1", 0.0005)["net"]
    base_s = bt.ann_sharpe(base.loc[(base.index >= OOS_START) & (base.index <= OOS_END)])
    b0 = bt.run_variant(bt.to_matrices(sig), "B0", 0.0005)["net"]
    b0_s = bt.ann_sharpe(b0.loc[(b0.index >= OOS_START) & (b0.index <= OOS_END)])
    observed = base_s - b0_s

    draws = []
    reb_idx = {sym: g.index[g["rebalance"].values] for sym, g in sig.groupby("symbol")}
    for _ in range(n_draws):
        s2 = sig.copy()
        for sym, idx in reb_idx.items():
            if len(idx) == 0:
                continue
            gate = s2.loc[idx, "gate_oi"].values.copy()
            RNG.shuffle(gate)
            s2.loc[idx, "gate_oi"] = gate
        s2["w_b1"] = np.where(s2["tradable"], s2["s"] * (1.0 / sig["symbol"].nunique()) * s2["gate_oi"], 0.0)
        net = bt.run_variant(bt.to_matrices(s2), "B1", 0.0005)["net"]
        draws.append(bt.ann_sharpe(net.loc[(net.index >= OOS_START) & (net.index <= OOS_END)]) - b0_s)
    draws = np.array(draws, dtype=float)
    p = float((draws >= observed).mean())
    return {"draws": n_draws, "observed_delta": observed, "null_mean": float(np.nanmean(draws)),
            "null_p95": float(np.nanpercentile(draws, 95)), "p_value": p,
            "interpretation": "p<0.05 表示门的信息含量超出「随机少交易」的机械效应"}


# ------------------------------------------------------------ 4. 波动匹配
def vol_matched(sig: pd.DataFrame) -> dict:
    r0 = bt.run_variant(bt.to_matrices(sig), "B0", 0.0005)
    r1 = bt.run_variant(bt.to_matrices(sig), "B1", 0.0005)
    v0 = r0["net"].loc[(r0.index >= IS_START) & (r0.index <= IS_END)].std()
    v1 = r1["net"].loc[(r1.index >= IS_START) & (r1.index <= IS_END)].std()
    k = float(v0 / v1) if v1 and np.isfinite(v1) else 1.0
    scaled = r1["net"] * k
    return {"scale": k,
            "oos_sharpe_B1_raw": bt.ann_sharpe(r1["net"].loc[OOS_START:OOS_END]),
            "oos_sharpe_B1_volmatched": bt.ann_sharpe(scaled.loc[OOS_START:OOS_END]),
            "oos_sharpe_B0": bt.ann_sharpe(r0["net"].loc[OOS_START:OOS_END]),
            "oos_ann_return_B0": float(r0["net"].loc[OOS_START:OOS_END].mean() * 252),
            "oos_ann_return_B1_raw": float(r1["net"].loc[OOS_START:OOS_END].mean() * 252),
            "oos_ann_return_B1_volmatched": float(scaled.loc[OOS_START:OOS_END].mean() * 252),
            "note": "Sharpe 对整体缩放不变，因此 P3 只回答「把 B1 放大到 B0 同波动后，收益与回撤是否仍更好」"}


# ------------------------------------------------------------ 5. 极值案例
def extreme_cases(cond: pd.DataFrame, top: int = 20) -> pd.DataFrame:
    t = cond[cond["tradable"]].copy()
    t["abs_mom"] = t["mom60"].abs()
    confirmed = t[t["gate_oi"] == 1]
    cases = []
    # (a) 最强趋势且被持仓确认
    a = confirmed.nlargest(top, "abs_mom").assign(case_type="strongest_confirmed_trend")
    # (b) 最强趋势但持仓不确认（门把最强的交易挡掉的那些）
    b = (t[t["gate_oi"] == 0].nlargest(top, "abs_mom")
         .assign(case_type="strongest_rejected_trend"))
    # (c) 持仓变化最剧烈且确认
    c = confirmed.nlargest(top, "oi_chg5").assign(case_type="largest_oi_expansion_confirmed")
    # (d) 持仓剧烈收缩但价格趋势仍强（背离）
    d = (t[(t["gate_oi"] == 0) & (t["oi_chg5"] < 0)].nsmallest(top, "oi_chg5")
         .assign(case_type="largest_oi_contraction_rejected"))
    for x in (a, b, c, d):
        cases.append(x)
    out = pd.concat(cases, ignore_index=True)
    return out[["case_type", "date", "symbol", "s", "mom60", "oi_chg5", "gate_oi", "gate_vol", "fwd5"]]


# ------------------------------------------------------------ 6. 风险识别
def roll_contamination(sig: pd.DataFrame) -> dict:
    """换月污染检查：主力合约切换会让 OI 出现水平跳变，需量化它影响多少决策。"""
    if "dominant_id" not in sig.columns:
        return {"available": False, "reason": "signals 中无 dominant_id 字段"}
    roll_flags = []
    for sym, g in sig.groupby("symbol", sort=True):
        g = g.sort_values("date")
        roll = g["dominant_id"].astype(str) != g["dominant_id"].astype(str).shift(1)
        roll.iloc[0] = False
        roll_flags.append(pd.DataFrame({"date": g["date"], "symbol": sym, "roll": roll.values,
                                        "rebalance": g["rebalance"].values,
                                        "oi_chg5": g["oi_chg5"].values}))
    rf = pd.concat(roll_flags, ignore_index=True)
    # 换月日及之后 5 个交易日视为「换月窗口」
    win = []
    for sym, g in rf.groupby("symbol", sort=True):
        g = g.sort_values("date").copy()
        g["win"] = g["roll"].rolling(5, min_periods=1).max().astype(bool)
        win.append(g)
    rf = pd.concat(win, ignore_index=True)

    reb = rf[rf["rebalance"]]
    in_win = reb[reb["win"]]
    oi_all = rf["oi_chg5"].replace([np.inf, -np.inf], np.nan)
    oi_win = oi_all[rf["win"] & oi_all.notna()].abs()
    oi_out = oi_all[~rf["win"] & oi_all.notna()].abs()
    return {
        "available": True,
        "roll_days": int(rf["roll"].sum()),
        "roll_window_share_of_days": round(float(rf["win"].mean()), 4),
        "rebalance_decisions_in_roll_window": int(len(in_win)),
        "rebalance_decisions_total": int(len(reb)),
        "rebalance_share_in_roll_window": round(float(len(in_win) / len(reb)), 4) if len(reb) else None,
        "mean_abs_oi_chg5_in_window": float(oi_win.mean()) if len(oi_win) else None,
        "mean_abs_oi_chg5_outside": float(oi_out.mean()) if len(oi_out) else None,
        "note": "换月窗口内 |ΔOI_5d| 若显著偏高，说明持仓门的通过率受换月水平跳变影响，结论需按窗口内外分别复核",
    }


def roll_excluded_backtest(sig: pd.DataFrame) -> dict:
    """剔除换月窗口后重跑：若 ΔSharpe 消失，说明增益来自换月造成的 OI 水平跳变。"""
    if "dominant_id" not in sig.columns:
        return {"available": False, "reason": "signals 中无 dominant_id 字段"}

    s = sig.copy()
    s["win"] = False
    for sym, g in s.groupby("symbol", sort=True):
        g = g.sort_values("date")
        roll = g["dominant_id"].astype(str) != g["dominant_id"].astype(str).shift(1)
        roll.iloc[0] = False
        w = roll.rolling(5, min_periods=1).max().astype(bool)
        s.loc[g.index, "win"] = w.values

    s2 = s.copy()
    for c in ("w_b0", "w_b1"):
        s2[c] = np.where(s2["win"], 0.0, s2[c])

    def run(df: pd.DataFrame, key: str) -> pd.Series:
        r = bt.run_variant(bt.to_matrices(df), key, 0.0005)["net"]
        return r.loc[(r.index >= OOS_START) & (r.index <= OOS_END)]

    b0_all = bt.run_variant(bt.to_matrices(s), "B0", 0.0005)["net"]
    b1_all = bt.run_variant(bt.to_matrices(s), "B1", 0.0005)["net"]
    b0_ex = run(s2, "B0")
    b1_ex = run(s2, "B1")
    d_all = (bt.ann_sharpe(b1_all.loc[OOS_START:OOS_END]) - bt.ann_sharpe(b0_all.loc[OOS_START:OOS_END]))
    d_ex = bt.ann_sharpe(b1_ex) - bt.ann_sharpe(b0_ex)
    ratio = (abs(d_ex) / abs(d_all)) if d_all and np.isfinite(d_all) else None

    if d_all == 0 or not np.isfinite(d_all):
        verdict = "基准差异为 0，无法判断"
    elif d_ex * d_all > 0 and (ratio or 0) >= 0.7:
        verdict = "同号且幅度保留 ≥70%：差异不是换月造成的"
    elif d_ex * d_all > 0:
        verdict = "同号但幅度明显缩小：部分来自换月窗口"
    else:
        verdict = "剔除换月窗口后符号翻转：负结果更强，不改变本报告结论"

    return {
        "available": True,
        "roll_window_days_dropped": int(s["win"].sum()),
        "delta_sharpe_oos_all": float(d_all),
        "delta_sharpe_oos_roll_excluded": float(d_ex),
        "delta_magnitude_ratio": ratio,
        "verdict": verdict,
    }


def risk_profile(sig: pd.DataFrame, cond: pd.DataFrame) -> dict:
    mats = bt.to_matrices(sig)
    run = bt.run_variant(mats, "B1", 0.0005)
    net = run["net"].loc[(run.index >= OOS_START) & (run.index <= OOS_END)]
    eq = (1 + net).cumprod()
    dd = eq / eq.cummax() - 1
    worst_date = str(dd.idxmin())
    # 回撤区间
    peak = eq.loc[:worst_date].idxmax() if worst_date in eq.index else None
    # 品种集中度：按贡献绝对值
    w = mats["B1"].fillna(0.0)
    r = mats["ret"].fillna(0.0)
    contrib = (w.shift(1).fillna(0.0) * r).loc[(r.index >= OOS_START) & (r.index <= OOS_END)]
    by_sym = contrib.sum().sort_values()
    gross_abs = float(contrib.abs().sum().sum())
    hhi = float(((by_sym.abs() / gross_abs) ** 2).sum()) if gross_abs else float("nan")
    return {
        "oos_max_dd": float(dd.min()), "oos_worst_date": worst_date,
        "oos_peak_date": str(peak), "oos_invested_share": float((w.abs().sum(axis=1) > 0).mean()),
        "avg_gross_exposure": float(w.abs().sum(axis=1).mean()),
        "top5_contributors": {k: float(v) for k, v in by_sym.tail(5).items()},
        "bottom5_contributors": {k: float(v) for k, v in by_sym.head(5).items()},
        "contrib_hhi": hhi,
        "note": "集中度用贡献绝对值 HHI；HHI 越高越依赖少数品种",
    }


def main() -> None:
    sig = pd.read_parquet(OUT / "signals.parquet")
    sig["date"] = sig["date"].astype(str)
    universe = sorted(sig["symbol"].unique())
    panel = pd.read_parquet(OUT / "panel.parquet")
    feat = ft.build_features(panel)

    report: Dict[str, object] = {"universe": universe, "n_symbols": len(universe)}
    print("[1/6] 未来函数：抽样重算")
    report["causal_check"] = causal_check(sig)
    print("      ", {k: report["causal_check"][k] for k in ("samples", "mismatches", "passed")})

    print("[2/6] 未来函数：偷看一日负向对照")
    report["lookahead_control"] = lag_injection_check(sig)
    print("      ", report["lookahead_control"])

    print("[3/6] 参数邻域（仅披露，不用于选择）")
    report["parameter_neighborhood"] = parameter_neighborhood(feat, universe)
    print("       cells:", len(report["parameter_neighborhood"]))

    print("[4/6] 置换检验")
    report["permutation"] = permutation_test(sig)
    print("      ", {k: report["permutation"][k] for k in ("observed_delta", "null_p95", "p_value")})

    print("[5/6] 波动匹配")
    report["vol_matched"] = vol_matched(sig)
    print("      ", report["vol_matched"])

    print("[6/6] 极值案例 + 风险识别")
    cond = pd.read_csv(OUT / "bt_conditional.csv")
    cases = extreme_cases(cond)
    cases.to_csv(OUT / "extreme_cases.csv", index=False, encoding="utf-8-sig")
    report["roll_contamination"] = roll_contamination(sig)
    report["roll_excluded"] = roll_excluded_backtest(sig)
    report["risk"] = risk_profile(sig, cond)
    report["extreme_case_counts"] = cases["case_type"].value_counts().to_dict()

    # 三层沙漏结论
    layer1 = report["causal_check"]["passed"]
    layer2 = "见 parameter_neighborhood：IS/OOS 衰减与 ΔOOS 一致性"
    report["sandglass"] = {
        "layer1_future_function": "PASS" if layer1 else "FAIL",
        "layer2_overfit": layer2,
        "layer3_oos": "见 bt_metrics.json 的 OOS 段；样本外仅评估一次，未参与任何选择",
    }
    (OUT / "validate_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=float),
                                              encoding="utf-8")
    (OUT / "permutation.json").write_text(json.dumps(report["permutation"], ensure_ascii=False, indent=2,
                                                     default=float), encoding="utf-8")
    print("\nvalidate_report.json 已写出")


if __name__ == "__main__":
    main()
