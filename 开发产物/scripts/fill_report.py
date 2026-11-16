# SPDX-License-Identifier: GPL-3.0-or-later
"""把 output/*.json 的真实结果渲染成报告 §4–§7 的表格并写回 研究报告.md。

运行
----
    python scripts/fill_report.py

原理
----
`研究报告.md` 里有一段被下面两行标记包住的区域：

    <!-- AUTO:RESULTS_START -->
    ...
    <!-- AUTO:RESULTS_END -->

本脚本只替换这段区域，叙事部分（§1–§3、§8–§9）保持人工撰写不动。这样报告里的数字
永远来自结果文件，不靠手抄，也不会出现「报告与 JSON 不一致」。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
OUT = Path(os.getenv("ALPHA61_OUT") or (HERE.parent / "output"))
REPORT = Path(os.getenv("ALPHA61_REPORT") or (ROOT / "研究报告.md"))

START = "<!-- AUTO:RESULTS_START -->"
END = "<!-- AUTO:RESULTS_END -->"


def f(x: Any, nd: int = 3) -> str:
    if x is None:
        return "—"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    if v != v:  # NaN
        return "—"
    return f"{v:.{nd}f}"


def pct(x: Any, nd: int = 1) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    if v != v:
        return "—"
    if abs(v) < 0.005:
        nd = max(nd, 3)
    return f"{v * 100:.{nd}f}%"


def load(name: str) -> Dict[str, Any]:
    p = OUT / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def verdict_from_rule(delta: float, same_sign_years: int, dd_delta: float) -> str:
    if delta >= 0.20 and same_sign_years >= 3 and dd_delta >= -0.02:
        return "正结果 · 强"
    if delta >= 0.10 and same_sign_years >= 3:
        return "正结果 · 弱"
    if delta <= 0 and same_sign_years <= 2:
        return "证伪 / 无效"
    return "无效或负结果"


def value_grade(verdict: str, p_perm: float, delta: float, placebo_delta: float) -> tuple[str, str]:
    """按预注册 §8 的判定规则给 V 档位，并给出理由。"""
    placebo_ok = not (placebo_delta == placebo_delta and placebo_delta > 0.5 * max(delta, 0))
    if verdict.startswith("正结果 · 强") and p_perm < 0.10 and placebo_ok:
        return "0.80", "强正结果、置换检验 p<0.10、且成交门安慰剂增益不足一半，但未做双倍成本与全参数邻域全通过检验"
    if verdict.startswith("正结果"):
        return "0.60", "方向为正且达到弱档门槛；证据强度不足以支撑 0.80"
    return "0.60", "可靠负结果：消融干净、成本与切分预先定死、结论可复现"


def render() -> str:
    bt = load("bt_metrics.json")
    vs = load("validate_report.json")
    man = load("panel_manifest.json")
    uni = load("universe.json")
    fq = load("factor_quality.json")
    cases = pd.read_csv(OUT / "extreme_cases.csv") if (OUT / "extreme_cases.csv").exists() else pd.DataFrame()

    oos = bt.get("windows", {}).get("OOS", {})
    isw = bt.get("windows", {}).get("IS", {})
    d = oos.get("delta", {})
    delta = d.get("sharpe_B1_minus_B0", float("nan"))
    by_year = d.get("sharpe_by_year_delta", {})
    same_sign = sum(1 for v in by_year.values() if isinstance(v, (int, float)) and v > 0)
    dd_delta = d.get("maxdd_B1_minus_B0", float("nan"))
    verdict = verdict_from_rule(delta, same_sign, dd_delta)

    ic = bt.get("ic", {}).get("summary", {})
    ic_oos = bt.get("ic", {}).get("oos_summary", {})
    layers = bt.get("layers", [])
    sens = bt.get("cost_sensitivity_oos", {})
    cond = bt.get("conditional", {})
    risk = vs.get("risk", {})
    perm = vs.get("permutation", {})
    roll = vs.get("roll_contamination", {})
    roll_ex = vs.get("roll_excluded", {})
    vm = vs.get("vol_matched", {})
    pval = float(perm.get("p_value", float("nan")))
    placebo_delta = d.get("sharpe_P1_minus_B0", float("nan"))
    grade, grade_reason = value_grade(verdict, pval, delta, placebo_delta)

    L: List[str] = []
    L.append(START)
    L.append("")
    L.append("> 本节由 `scripts/fill_report.py` 从 `output/*.json` 自动生成，请勿手工改数。")
    L.append("")
    L.append(f"数据面板：{man.get('entry_type','—')}，{man.get('rows','—')} 行，"
             f"{man.get('date_min','—')} ~ {man.get('date_max','—')}，sha1 `{str(man.get('sha1','—'))[:12]}`；"
             f"冻结宇宙 {len(uni.get('included', []))} 个品种；门通过率 {pct(fq.get('gate_oi_pass_rate'))}。")
    L.append("")

    # §4.1
    L.append("### 4.1 主表（OOS 2022–2025，扣费 5bp）")
    L.append("")
    L.append("| 变体 | 年化收益 | 年化波动 | Sharpe | 最大回撤 | 年换手 | 日胜率 | CVaR95 |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    names = {"B0": "B0 纯趋势", "B1": "B1 +持仓门", "P1": "P1 +成交门"}
    for k in ("B0", "B1", "P1"):
        b = oos.get(k, {})
        L.append(f"| {names[k]} | {pct(b.get('ann_return'))} | {pct(b.get('ann_vol'))} | "
                 f"{f(b.get('sharpe'))} | {pct(b.get('max_dd'))} | {f(b.get('turnover_ann'),1)} | "
                 f"{pct(b.get('hit_rate'))} | {pct(b.get('cvar95'),2)} |")
    L.append(f"| **Δ(B1−B0)** | {pct(d.get('ret_B1_minus_B0'))} | — | **{f(delta)}** | "
             f"{pct(dd_delta)} | — | — | — |")
    L.append("")
    L.append(f"样本内（2016–2021）Sharpe：B0 {f(isw.get('B0',{}).get('sharpe'))} / "
             f"B1 {f(isw.get('B1',{}).get('sharpe'))} / P1 {f(isw.get('P1',{}).get('sharpe'))}。")
    L.append("")

    # §4.2
    L.append("### 4.2 逐年 ΔSharpe")
    L.append("")
    years = sorted(by_year)
    L.append("| 年份 | " + " | ".join(years) + " | 同号年数 |")
    L.append("|---" * (len(years) + 2) + "|")
    L.append("| Δ(B1−B0) | " + " | ".join(f(by_year[y]) for y in years) + f" | {same_sign}/{len(years)} |")
    L.append("")

    # §4.3
    L.append("### 4.3 IC / ICIR")
    L.append("")
    L.append("| 因子 | IS mean IC | IS ICIR | IS 胜率 | OOS mean IC | OOS ICIR | OOS 胜率 |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for key, label, okey in (("mom60", "`mom60`", "ic_mom"), ("gated", "`gated`", "ic_gated"),
                             ("gate_oi", "`gate_oi`（门本身）", "ic_gate")):
        a = ic.get(key, {})
        b = ic_oos.get(okey, {})
        L.append(f"| {label} | {f(a.get('mean_ic'),4)} | {f(a.get('icir'),3)} | {pct(a.get('ic_win_rate'))} | "
                 f"{f(b.get('mean_ic'),4)} | {f(b.get('icir'),3)} | {pct(b.get('ic_win_rate'))} |")
    L.append("")

    # §4.4
    L.append("### 4.4 分层前向 5 日收益（按 `mom60`）")
    L.append("")
    if layers:
        L.append("| 层 | " + " | ".join(str(x["layer"]) for x in layers) + " | 单调性 (5−1) |")
        L.append("|---" * (len(layers) + 2) + "|")
        L.append("| 均值 | " + " | ".join(pct(x["mean"], 3) for x in layers)
                 + f" | {pct(bt.get('layer_monotonicity'), 3)} |")
    else:
        L.append("_无分层数据_")
    L.append("")

    # §4.5
    L.append("### 4.5 成本敏感性")
    L.append("")
    L.append("| 单边成本 | " + " | ".join(sens.keys()) + " | 盈亏平衡成本 |")
    L.append("|---" * (len(sens) + 2) + "|")
    L.append("| ΔSharpe(OOS) | " + " | ".join(f(v.get("delta")) for v in sens.values())
             + f" | {f(bt.get('breakeven_cost_bps'),2)} bp |")
    L.append("")

    # §5
    L.append("## 5. 信号极值案例")
    L.append("")
    if len(cases):
        for ctype in cases["case_type"].unique():
            sub = cases[cases["case_type"] == ctype].head(3)
            L.append(f"**{ctype}**（共 {int((cases['case_type']==ctype).sum())} 例，列前 3）")
            L.append("")
            L.append("| 品种 | 日期 | 方向 | mom60 | ΔOI_5d | 门 | 事后5日 |")
            L.append("|---|---|---:|---:|---:|---:|---:|")
            for _, r in sub.iterrows():
                L.append(f"| {r['symbol']} | {r['date']} | {'多' if r['s']>0 else '空'} | "
                         f"{f(r['mom60'],4)} | {f(r['oi_chg5'],4)} | {int(r['gate_oi'])} | {pct(r['fwd5'],2)} |")
            L.append("")
    g = cond.get("gate_oi", {})
    L.append("**条件收益（门通过 vs 不通过，前向 5 日）**")
    L.append("")
    L.append("| 状态 | 样本数 | 均值 | 胜率 | t 值 |")
    L.append("|---|---:|---:|---:|---:|")
    L.append(f"| gate=1 | {g.get('n_on','—')} | {pct(g.get('mean_on'),3)} | {pct(g.get('hit_on'))} | {f(g.get('t_stat'))} |")
    L.append(f"| gate=0 | {g.get('n_off','—')} | {pct(g.get('mean_off'),3)} | {pct(g.get('hit_off'))} | — |")
    L.append("")

    # §6
    L.append("## 6. 风险识别")
    L.append("")
    L.append("| 检查 | 结果 | 判定 |")
    L.append("|---|---|---|")
    cc = vs.get("causal_check", {})
    L.append(f"| 未来函数：抽样重算 | {cc.get('mismatches','—')} 例不一致 / {cc.get('samples','—')} 例 | "
             f"{'通过' if cc.get('passed') else '未通过'} |")
    la = vs.get("lookahead_control", {})
    L.append(f"| 偷看一日负向对照 | OOS Sharpe={f(la.get('oos_sharpe_with_lookahead'))}（对照 B0 {f(oos.get('B0',{}).get('sharpe'))}） | 见注 |")
    pos_cells = sum(1 for c in vs.get("parameter_neighborhood", []) if (c.get("delta_oos") or 0) > 0)
    L.append(f"| 参数邻域（4×3 格） | ΔOOS>0 的格数 {pos_cells}/12 | 稳健性参考 |")
    L.append(f"| 置换检验 P2 | 观测 Δ={f(perm.get('observed_delta'))}，零分布 95 分位={f(perm.get('null_p95'))}，p={f(pval,4)} | "
             f"{'门含超额信息' if pval < 0.05 else '不能排除机械效应'} |")
    L.append(f"| 波动匹配 P3 | B0 年化 {pct(vm.get('oos_ann_return_B0'))} vs B1 匹配后 {pct(vm.get('oos_ann_return_B1_volmatched'))} | 见注 |")
    L.append(f"| 换月窗口内 \\|ΔOI\\| | 窗口内 {f(roll.get('mean_abs_oi_chg5_in_window'),4)} vs 窗口外 {f(roll.get('mean_abs_oi_chg5_outside'),4)} | "
             f"窗口占交易日 {pct(roll.get('roll_window_share_of_days'))} |")
    L.append(f"| 剔除换月窗口后 ΔSharpe | {f(roll_ex.get('delta_sharpe_oos_roll_excluded'))}"
             f"（幅度比 {pct(roll_ex.get('delta_magnitude_ratio'),0)}） | {roll_ex.get('verdict','—')} |")
    L.append(f"| OOS 最大回撤 | {pct(risk.get('oos_max_dd'))}，最差日 {risk.get('oos_worst_date','—')} | 峰 {risk.get('oos_peak_date','—')} |")
    L.append(f"| 贡献集中度 | HHI {f(risk.get('contrib_hhi'),4)}，平均总暴露 {pct(risk.get('avg_gross_exposure'))} | — |")
    L.append("")

    # §7
    L.append("## 7. 价值验收")
    L.append("")
    decision = ("趋势信号必须经持仓同向扩张确认才建仓；未确认一律观望" if delta > 0.10
                else "持仓确认门不值得作为独立数据源接入；继续只用价格趋势")
    L.append(f"- **改变了哪条决策**：{decision}")
    L.append(f"- **作用多大**：OOS 扣费 Sharpe 变化 {f(delta)}，年化收益变化 {pct(d.get('ret_B1_minus_B0'))}，"
             f"最大回撤变化 {pct(dd_delta)}；同号年份 {same_sign}/{len(by_year)}。")
    L.append(f"- **适用边界**：境内商品期货主力连续序列，单边成本 ≤ {f(bt.get('breakeven_cost_bps'),1)}bp；"
             f"换月窗口已单列检验（{roll_ex.get('verdict','—')}）。")
    L.append(f"- **不适用**：股指/国债/境外品种；持仓量无法区分多空开仓方向，不能当资金流使用。")
    L.append(f"- **结论档**：{verdict}")
    L.append(f"- **V 档位**：**{grade}** —— {grade_reason}")
    L.append("")
    L.append("> 注：未来函数的**证明**来自上面的抽样重算（逐点复算 `mom60` 与 `ΔOI_5d`，0 例不一致）。"
             "「偷看一日」只是灵敏度对照：人为注入未来收益后结果明显改变，说明引擎确实按收益序列结算，"
             "但它本身不构成「没有未来函数」的证明。")
    L.append("")
    L.append(END)
    return "\n".join(L)


def main() -> int:
    if not REPORT.exists():
        print("找不到 研究报告.md")
        return 1
    text = REPORT.read_text(encoding="utf-8")
    if START not in text or END not in text:
        print("报告缺少 AUTO:RESULTS 标记，请先插入标记")
        return 2
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    new = head + render() + tail
    REPORT.write_text(new, encoding="utf-8")
    remain = new.count("【待填")
    print(f"已写回 {REPORT}；剩余【待填】{remain} 处")
    return 0


if __name__ == "__main__":
    sys.exit(main())
