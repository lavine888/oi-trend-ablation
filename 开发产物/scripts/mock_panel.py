"""合成数据干跑：在不接触平台的情况下验证 factor/backtest/validate 全链路。

⚠️ 用途仅限**代码正确性与统计功效自检**。合成数据里人为植入了「持仓确认有效」的
效应，因此这里跑出的任何数字都**不能**当作 Alpha-61 的绩效证据，也不得写入报告。

运行
----
    python scripts/mock_panel.py
    $env:ALPHA61_OUT=".../output_mock"
    python scripts/factor.py --reuse-panel
    python scripts/backtest.py
    python scripts/validate.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import factor as ft  # noqa: E402

RNG = np.random.default_rng(42)


def main() -> None:
    out = Path(os.getenv("ALPHA61_OUT") or (ft.ROOT / "output_mock"))
    out.mkdir(parents=True, exist_ok=True)

    dates = pd.bdate_range("2016-01-04", "2025-12-31")
    varieties = ["CU", "AL", "ZN", "RB", "I", "J", "M", "Y", "P", "SR", "TA", "MA"]
    exch = {"CU": "SHF", "AL": "SHF", "ZN": "SHF", "RB": "SHF", "I": "DCE", "J": "DCE",
            "M": "DCE", "Y": "DCE", "P": "DCE", "SR": "CZC", "TA": "CZC", "MA": "CZC"}

    frames = []
    for v in varieties:
        sym = f"{v}_DOMINANT.{exch[v]}"
        n = len(dates)
        # 趋势状态：缓慢变化的动量方向
        mom_state = np.sign(np.sin(np.linspace(0, 18 * np.pi, n) + RNG.uniform(0, 6.28)))
        informed = RNG.random(n) < 0.62          # 持仓是否"确认"了趋势
        noise = RNG.normal(0, 0.011, n)
        # 植入效应：确认日额外 +3bp 的漂移
        drift = 0.0003 * mom_state * informed
        ret = mom_state * 0.0004 + drift + noise

        settlement = 4000 * np.exp(np.cumsum(ret))
        pre_settlement = np.concatenate([[settlement[0]], settlement[:-1]])
        # 持仓量：确认日同向扩张
        oi_chg = 0.01 * mom_state * informed + RNG.normal(0, 0.02, n)
        oi = 1e6 * np.exp(np.cumsum(oi_chg))

        frames.append(pd.DataFrame({
            "symbol": sym, "date": dates.strftime("%Y-%m-%d"),
            "open": settlement, "high": settlement * 1.01, "low": settlement * 0.99, "close": settlement,
            "volume": RNG.integers(50_000, 500_000, n).astype(float),
            "dominant_id": v + "2501", "underlying_symbol": v, "trading_code": v.lower() + "2501",
            "exchange": exch[v], "limit_up": settlement * 1.07, "limit_down": settlement * 0.93,
            "open_interest": oi, "day_session_open": settlement,
            "amount": RNG.uniform(2e8, 3e9, n), "settlement": settlement,
            "pre_settlement": pre_settlement,
        }))

    panel = pd.concat(frames, ignore_index=True)
    panel.to_parquet(out / "panel.parquet", index=False)
    print(f"[mock] {len(panel)} 行写入 {out / 'panel.parquet'}")
    print("[mock] 注意：合成数据植入了正效应，结果不得作为绩效证据")


if __name__ == "__main__":
    main()
