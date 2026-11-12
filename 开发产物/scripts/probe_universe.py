"""探测平台可用性：合约池、日线字段、持仓接口。

只做只读探测并留痕，不产出任何因子结论。运行：
    python scripts/probe_universe.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pd_client


def main() -> None:
    info = pd_client.bootstrap(force=True)
    print("[bootstrap]", {k: v for k, v in info.items() if k != "base_url"})

    print("\n=== 1. 合约池 (get_future_detail) ===")
    try:
        det = pd_client.future_detail()
        print("rows:", len(det), "cols:", list(det.columns)[:20])
        print(det.head(3).to_string()[:1500])
    except Exception as exc:  # noqa: BLE001
        print("future_detail failed:", type(exc).__name__, exc)

    print("\n=== 2. 主力合约日线 (get_future_dominant) ===")
    try:
        dom = pd_client.future_dominant(start_date="20250101", end_date="20250131")
        print("rows:", len(dom), "cols:", list(dom.columns)[:25])
        if len(dom):
            print(dom.head(3).to_string()[:1500])
    except Exception as exc:  # noqa: BLE001
        print("future_dominant failed:", type(exc).__name__, exc)

    print("\n=== 3. 单合约日线含持仓 (get_future_daily) ===")
    df = pd_client.future_daily(["A_DOMINANT.DCE"], "20250101", "20250131")
    print("rows:", len(df), "cols:", list(df.columns))
    print(df.head(3).to_string())

    print("\n=== 4. 席位持仓 (get_future_symbol_posi) ===")
    try:
        posi = pd_client.future_symbol_posi(start_date="20250102", end_date="20250110")
        print("rows:", len(posi), "cols:", list(posi.columns)[:25])
        if len(posi):
            print(posi.head(5).to_string()[:2000])
    except Exception as exc:  # noqa: BLE001
        print("symbol_posi failed:", type(exc).__name__, exc)

    print("\n=== 5. 历史深度 ===")
    deep = pd_client.future_daily(["A_DOMINANT.DCE"], "20160101", "20251231")
    print("A_DOMINANT 2016-2025 rows:", len(deep), "date range:",
          getattr(deep.get("date", pd.Series(dtype=str)).min(), "item", lambda: None)(),
          "->",
          getattr(deep.get("date", pd.Series(dtype=str)).max(), "item", lambda: None)())


if __name__ == "__main__":
    main()
