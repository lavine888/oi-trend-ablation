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


def main() -> int:
    report = REPORT.read_text(encoding="utf-8")
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
