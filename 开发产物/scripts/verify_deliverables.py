# SPDX-License-Identifier: GPL-3.0-or-later
"""交付门禁：逐项核对 Alpha-61 是否达到「可交付」状态，并打印证据表。

运行
----
    python scripts/verify_deliverables.py

退出码
------
    0 = 全部通过；1 = 存在未通过项（含「结果段仍待填」）

设计目的：把「我觉得做完了」换成「证据显示做完了」。任何一项没通过都不允许宣称交付。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent          # alpha-61-.../
DEV = ROOT / "开发产物"
OUT = DEV / "output"
PROD = ROOT / "生产产物"

RESULTS: list[tuple[str, bool, str]] = []


def mark(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def check_files() -> None:
    need_dev = ["SKILL.md", "scripts/factor.py", "scripts/validate.py", "scripts/backtest.py",
                "references/data_guide.md", "scripts/build_production.py", "scripts/pd_client.py"]
    for rel in need_dev:
        p = DEV / rel
        mark(f"开发产物存在 {rel}", p.exists(), "" if p.exists() else "缺失")

    need_prod = ["SKILL.md", "数据库.parquet"]
    for rel in need_prod:
        p = PROD / rel
        mark(f"生产产物存在 {rel}", p.exists(), "" if p.exists() else "缺失")

    mark("预注册文件存在", (ROOT / "00_预注册_研究设计与口径.md").exists())
    mark("研究报告存在", (ROOT / "研究报告.md").exists())


def check_freeze() -> None:
    rec = ROOT / "00_预注册_冻结记录.json"
    spec = ROOT / "00_预注册_研究设计与口径.md"
    if not (rec.exists() and spec.exists()):
        mark("预注册冻结记录", False, "文件缺失")
        return
    frozen = json.loads(rec.read_text(encoding="utf-8-sig"))
    actual = hashlib.sha256(spec.read_bytes()).hexdigest().upper()
    mark("预注册未被事后修改（哈希一致）", actual == frozen.get("sha256", "").upper(),
         f"记录={frozen.get('sha256','')[:12]} 实际={actual[:12]}")


def _iso(d: str) -> str:
    """把 YYYYMMDD / YYYY-MM-DD 统一成 YYYY-MM-DD。"""
    s = str(d).replace("-", "")
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 and s[:8].isdigit() else str(d)


def check_panel() -> None:
    man = OUT / "panel_manifest.json"
    if not man.exists():
        # 保持检查项总数恒定，便于对照两次运行
        mark("面板清单存在", False, "尚未拉取平台数据")
        mark("数据入口为平台接口直取", False, "无面板清单")
        mark("面板覆盖样本内+样本外", False, "无面板清单")
        return
    m = json.loads(man.read_text(encoding="utf-8"))
    dmin, dmax = _iso(m.get("date_min", "")), _iso(m.get("date_max", ""))
    mark("面板清单存在", True, f"{m.get('rows')} 行 / {dmin}~{dmax}")
    mark("数据入口为平台接口直取", m.get("entry_type") == "panda_data_api",
         f"entry_type={m.get('entry_type')}")
    mark("面板覆盖样本内+样本外", dmin <= "2016-01-05" and dmax >= "2025-12-01",
         f"{dmin}~{dmax}")


def check_calls() -> None:
    log = DEV / "logs" / "platform_calls.jsonl"
    if not log.exists():
        mark("平台调用留痕", False, "无日志")
        return
    ok_calls, total = 0, 0
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("event") == "call":
            total += 1
            if r.get("rows"):
                ok_calls += 1
    mark("平台调用留痕（含成功返回）", ok_calls > 0, f"成功 {ok_calls} / 记录 {total}")


def check_results() -> None:
    for name in ("bt_metrics.json", "validate_report.json", "extreme_cases.csv",
                 "bt_ic.csv", "bt_layers.csv", "bt_equity.csv"):
        p = OUT / name
        mark(f"结果文件 {name}", p.exists())

    bt = OUT / "bt_metrics.json"
    if not bt.exists():
        for name in ("OOS 三变体指标齐全", "成本敏感性与盈亏平衡已算", "IC/ICIR 已算", "分层收益已算"):
            mark(name, False, "无 bt_metrics.json")
    else:
        d = json.loads(bt.read_text(encoding="utf-8"))
        oos = d.get("windows", {}).get("OOS", {})
        has_all = all(k in oos for k in ("B0", "B1", "P1")) and "delta" in oos
        mark("OOS 三变体指标齐全", has_all)
        mark("成本敏感性与盈亏平衡已算", "cost_sensitivity_oos" in d and "breakeven_cost_bps" in d)
        mark("IC/ICIR 已算", bool(d.get("ic", {}).get("summary")))
        mark("分层收益已算", bool(d.get("layers")))

    vs = OUT / "validate_report.json"
    if not vs.exists():
        for name in ("未来函数检测通过", "置换检验已跑", "换月污染与剔除检验已跑"):
            mark(name, False, "无 validate_report.json")
    else:
        v = json.loads(vs.read_text(encoding="utf-8"))
        mark("未来函数检测通过", v.get("causal_check", {}).get("passed") is True,
             f"不一致 {v.get('causal_check', {}).get('mismatches')} 例")
        mark("置换检验已跑", bool(v.get("permutation")))
        mark("换月污染与剔除检验已跑",
             bool(v.get("roll_contamination", {}).get("available"))
             and bool(v.get("roll_excluded", {}).get("available")))


def check_production() -> None:
    pq = PROD / "数据库.parquet"
    man = PROD / "build_manifest.json"
    if not pq.exists():
        mark("生产 Parquet 存在", False, "缺失")
        mark("生产 Parquet 字段合规", False, "缺失")
        mark("生产主键唯一", False, "缺失")
        mark("生产信号枚举合法", False, "缺失")
        mark("生产质量检查全通过", False, "缺失")
        mark("生产写入读回一致", False, "缺失")
        return
    df = pd.read_parquet(pq)
    need = ["trade_date", "asset_type", "symbol", "factor_id", "factor_name",
            "factor_value", "score", "signal", "data_version", "update_time"]
    missing = [c for c in need if c not in df.columns]
    mark("生产 Parquet 字段合规", not missing, f"缺 {missing}" if missing else f"{len(df)} 行")
    mark("生产主键唯一", not df.duplicated(subset=["trade_date", "symbol", "factor_id"]).any())
    mark("生产信号枚举合法", set(df["signal"]).issubset({"buy", "sell", "hold"}))
    if man.exists():
        m = json.loads(man.read_text(encoding="utf-8"))
        q = m.get("quality_checks", {})
        mark("生产质量检查全通过", q.get("all_passed") is True)
        mark("生产写入读回一致", m.get("readback_ok") is True)


def check_report_complete() -> None:
    rp = ROOT / "研究报告.md"
    if not rp.exists():
        mark("报告无待填项", False, "报告缺失")
        return
    text = rp.read_text(encoding="utf-8")
    n = text.count("【待填")
    mark("报告无待填项", n == 0, f"仍有 {n} 处【待填】")


def main() -> int:
    check_files()
    check_freeze()
    check_panel()
    check_calls()
    check_results()
    check_production()
    check_report_complete()

    width = max(len(n) for n, _, _ in RESULTS)
    print(f"{'检查项'.ljust(width)}  结果   证据")
    print("-" * (width + 30))
    for name, ok, detail in RESULTS:
        print(f"{name.ljust(width)}  {'PASS' if ok else 'FAIL'}   {detail}")

    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n合计 {len(RESULTS)} 项，通过 {len(RESULTS)-len(failed)} 项，未通过 {len(failed)} 项")
    if failed:
        print("未通过：" + "；".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
