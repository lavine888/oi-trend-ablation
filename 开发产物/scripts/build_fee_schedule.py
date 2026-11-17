# SPDX-License-Identifier: GPL-3.0-or-later
"""生成逐品种费率表：乘数取平台字段，最小变动价位与手续费取公开资料（近似）。

为什么分两段
------------
- `contract_multiplier` 来自 `panda_data.get_future_detail`，**平台可核**；
- `tick_size` 与手续费是公开资料整理，**平台不提供**，因此单独标 `confidence`，
  并在报告里以「情景 + 敏感性」的方式使用，不当作精确值。

运行：
    python 开发产物/scripts/build_fee_schedule.py
产出：
    references/fee_schedule.csv
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pd_client  # noqa: E402

ROOT = HERE.parent.parent
OUT = Path(os.getenv("ALPHA61_OUT") or (ROOT / "开发产物" / "output"))
REF = ROOT / "references"

# 品种 -> (最小变动价位, 手续费模式, 手续费值, 置信度)
# 模式 rate_bp = 成交名义额的万分之几(bp)；per_lot = 元/手（单边）
FEES = {
    "CU": (10, "rate_bp", 0.5, "high"),
    "AL": (5, "per_lot", 3.0, "high"),
    "ZN": (5, "per_lot", 3.0, "high"),
    "PB": (5, "rate_bp", 0.4, "high"),
    "NI": (10, "per_lot", 3.0, "high"),
    "SN": (10, "per_lot", 3.0, "medium"),
    "AU": (0.02, "per_lot", 10.0, "high"),
    "AG": (1, "rate_bp", 0.5, "high"),
    "RB": (1, "rate_bp", 1.0, "high"),
    "HC": (1, "rate_bp", 1.0, "high"),
    "BU": (1, "rate_bp", 1.0, "medium"),
    "FU": (1, "rate_bp", 0.5, "medium"),
    "RU": (5, "per_lot", 3.0, "high"),
    "A": (1, "per_lot", 2.0, "high"),
    "B": (1, "per_lot", 1.0, "medium"),
    "M": (1, "per_lot", 1.5, "high"),
    "Y": (2, "per_lot", 2.5, "high"),
    "P": (2, "per_lot", 2.5, "high"),
    "C": (1, "per_lot", 1.2, "high"),
    "CS": (1, "per_lot", 1.5, "medium"),
    "JD": (1, "per_lot", 1.5, "low"),
    "L": (1, "per_lot", 1.0, "high"),
    "V": (1, "per_lot", 1.0, "high"),
    "PP": (1, "per_lot", 1.0, "high"),
    "J": (0.5, "rate_bp", 1.0, "medium"),
    "JM": (0.5, "rate_bp", 1.0, "medium"),
    "I": (0.5, "rate_bp", 1.0, "high"),
    "SR": (1, "per_lot", 3.0, "high"),
    "CF": (5, "per_lot", 4.3, "high"),
    "TA": (2, "per_lot", 3.0, "high"),
    "MA": (1, "per_lot", 2.0, "high"),
    "OI": (1, "per_lot", 2.0, "medium"),
    "RM": (1, "per_lot", 1.5, "high"),
    "SF": (2, "per_lot", 3.0, "medium"),
    "SM": (2, "per_lot", 3.0, "medium"),
    "FG": (1, "per_lot", 6.0, "medium"),
}


def main() -> int:
    uni = json.loads((OUT / "universe.json").read_text(encoding="utf-8"))["included"]
    pd_client.bootstrap()
    import panda_data

    det = panda_data.get_future_detail(symbol=uni)
    det["product"] = det["underlying_symbol"].astype(str)

    rows = []
    for _, r in det.iterrows():
        prod = r["product"]
        if prod not in FEES:
            print(f"  跳过 {prod}（无公开费率条目）")
            continue
        tick, mode, val, conf = FEES[prod]
        rows.append({
            "product": prod,
            "symbol": r["symbol"],
            "contract_multiplier": float(r["contract_multiplier"]),
            "tick_size": tick,
            "fee_mode": mode,
            "fee_value": val,
            "confidence": conf,
            "multiplier_source": "panda_data.get_future_detail",
            "fee_source": "公开费率表近似（2025），使用前请按自身券商核对",
        })

    df = pd.DataFrame(rows).sort_values("product")
    REF.mkdir(exist_ok=True)
    target = REF / "fee_schedule.csv"
    df.to_csv(target, index=False, encoding="utf-8-sig")
    print(df.to_string(index=False))
    print(f"\n写出 {target}（{len(df)} 个品种；置信度分布 {df['confidence'].value_counts().to_dict()}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
