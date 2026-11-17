# SPDX-License-Identifier: GPL-3.0-or-later
"""文档/证据一致性检查：确认报告里的关键数字与 `results/` 里的原始结果一致。

这个脚本是给 CI 用的，不联网、不需要平台凭据，只读 `results/` 与仓库文件。

检查项：
1. `results/bt_metrics.json` 的 OOS ΔSharpe 与盈亏平衡成本，能在 `研究报告.md` 里找到对应文字；
2. 预注册文件的 sha256 与 `00_预注册_冻结记录.json` 一致（防止事后改口径）；
3. `生产产物/数据库.parquet` 的 schema、主键唯一性、信号枚举与行数；
4. `results/panel_manifest.json` 的面板指纹在报告里出现。

运行
----
    python 开发产物/scripts/check_report_consistency.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
RESULTS = ROOT / "results"
REPORT = ROOT / "研究报告.md"
PROD = ROOT / "生产产物" / "数据库.parquet"

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def norm(s: str) -> str:
    """统一减号：报告里可能用 U+2212，脚本输出用 ASCII。"""
    return s.replace("\u2212", "-")


def main() -> int:
    report = norm(REPORT.read_text(encoding="utf-8"))
    bt = json.loads((RESULTS / "bt_metrics.json").read_text(encoding="utf-8"))

    delta = bt["windows"]["OOS"]["delta"]["sharpe_B1_minus_B0"]
    be = bt["breakeven_cost_bps"]
    check("OOS ΔSharpe 数值", abs(delta - (-0.0281)) < 1e-3, f"results={delta:.4f}")
    check("盈亏平衡成本", abs(be - 4.4) < 0.05, f"results={be:.2f}bp")
    check("报告引用 ΔSharpe", f"{delta:.3f}" in report, "报告中应出现该数值")
    check("报告引用盈亏平衡", f"{be:.1f}" in report, "报告中应出现该数值")

    panel = json.loads((RESULTS / "panel_manifest.json").read_text(encoding="utf-8"))
    check("面板指纹出现在报告", str(panel["sha1"])[:12] in report, panel["sha1"][:12])
    check("面板行数与报告一致", "118,555" in report or "118555" in report, str(panel["rows"]))

    repl_path = RESULTS / "replication_cffex.json"
    if repl_path.exists():
        repl = json.loads(repl_path.read_text(encoding="utf-8"))
        rd = repl["costs"]["5bp"]["delta_b1_minus_b0"]
        check("外部复现 ΔSharpe 为负", rd < 0, f"CFFEX Δ={rd:.3f}")
        check("报告引用复现数值", f"{rd:.3f}" in report, "报告中应出现该数值")
        check("复现品种数", len(repl["universe"]) >= 4, str(len(repl["universe"])))
    else:
        check("外部复现结果存在", False, "缺 results/replication_cffex.json")

    # 2010–2015 时间维度复现
    r2p = RESULTS / "replication_2010_2015.json"
    if r2p.exists():
        r2 = json.loads(r2p.read_text(encoding="utf-8"))
        d2 = r2["costs"]["5bp"]["delta_b1_minus_b0"]
        check("2010–2015 复现为负", d2 < 0, f"Δ={d2:.3f}")
        check("报告引用 2010–2015 复现数值", f"{d2:.3f}" in report, "报告中应出现该数值")
        sp = r2.get("splits", {})
        if sp:
            worst = max(v["5bp"]["delta_b1_minus_b0"] for v in sp.values())
            check("三组切分全部为负", worst < 0, f"最不负 {worst:+.3f}")
            yr = sp.get("S0_原冻结", {}).get("yearly_delta", {})
            if yr:
                check("逐年全为负", max(yr.values()) < 0,
                      " / ".join(f"{y}:{v:+.2f}" for y, v in yr.items()))
    else:
        check("2010–2015 复现结果存在", False, "缺 results/replication_2010_2015.json")

    # 持仓替代构造：不允许有构造越过 +0.10 门槛
    ov = RESULTS / "oi_variants.json"
    if ov.exists():
        o = json.loads(ov.read_text(encoding="utf-8"))
        best = max((v["delta_vs_B0"] for k, v in o["results"].items() if not k.startswith("B0")), default=-9)
        check("无持仓构造越过 +0.10", best < 0.10, f"最佳 Δ={best:+.3f}")
        check("反向门同样为负", o["results"]["V4_反向门"]["delta_vs_B0"] < 0,
              f"{o['results']['V4_反向门']['delta_vs_B0']:+.3f}")
    else:
        check("持仓替代构造结果存在", False, "缺 results/oi_variants.json")

    # 逐品种费率：必须如实记录"符号随成本翻转"
    cf = RESULTS / "cost_real_fees.json"
    if cf.exists():
        c = json.loads(cf.read_text(encoding="utf-8"))
        lo = c["scenarios"]["fees_only"]["delta"]
        hi = c["scenarios"]["fees_plus_2ticks"]["delta"]
        check("费率检验记录了符号翻转", lo > 0 > hi, f"fees_only {lo:+.3f} / +2ticks {hi:+.3f}")
        check("报告披露成本敏感性", "4.4bp" in report, "报告中应出现盈亏平衡成本")
    else:
        check("逐品种费率结果存在", False, "缺 results/cost_real_fees.json")

    frozen = json.loads((ROOT / "00_预注册_冻结记录.json").read_text(encoding="utf-8"))
    spec = ROOT / "00_预注册_研究设计与口径.md"
    actual = hashlib.sha256(spec.read_bytes()).hexdigest().upper()
    check("预注册未被事后修改", actual == frozen["sha256"].upper(),
          f"记录 {frozen['sha256'][:12]} / 实际 {actual[:12]}")

    if PROD.exists():
        import pandas as pd

        df = pd.read_parquet(PROD)
        need = {"trade_date", "asset_type", "symbol", "factor_id", "factor_name",
                "factor_value", "score", "signal", "data_version", "update_time"}
        check("生产 Parquet 字段齐全", need.issubset(df.columns), f"rows={len(df)}")
        check("生产主键唯一", not df.duplicated(subset=["trade_date", "symbol", "factor_id"]).any())
        check("生产信号枚举合法", set(df["signal"]).issubset({"buy", "sell", "hold"}),
              str(df["signal"].value_counts().to_dict()))
    else:
        check("生产 Parquet 存在", False, "缺失")

    print(f"\n{'全部通过' if not FAILS else '未通过：' + '；'.join(FAILS)}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
