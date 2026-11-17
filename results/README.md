# results —— 报告里每个数字的原始出处

这个目录是**结果证据包**，不是数据包。它让 `研究报告.md` 里的每个数字都能被独立核对，
而不用重新跑一遍平台取数。

| 文件 | 里面是什么 | 对应报告 |
|---|---|---|
| `panel_manifest.json` | 面板指纹：118,555 行、55 个主力连续序列、2016-01-04 ~ 2025-12-31、`sha1 d613814cd3cf`、逐段调用记录 | §2.1 |
| `universe.json` | 36 个入池品种 + 逐品种纳入/剔除原因（只用样本内判定） | §2.2 |
| `factor_quality.json` | 缺失率、可交易占比、门通过率 49.3% | §4 |
| `bt_metrics.json` | B0/B1/P1 的 IS/OOS 指标、逐年 ΔSharpe、IC/ICIR、分层、成本敏感性、盈亏平衡 4.4bp | §4.1–4.5 |
| `bt_equity.csv` | 逐日扣费净值（三条曲线） | §4.1 |
| `bt_ic.csv` | 逐调仓日的横截面 IC（`mom60` / `gated` / `gate_oi`） | §4.3 |
| `bt_layers.csv` | 5 层前向收益 | §4.4 |
| `extreme_cases.csv` | 4 类信号极值案例各 20 条（品种/日期/字段值/事后 5 日） | §5 |
| `permutation.json` | 500 次随机门的零分布、观测 Δ、p 值 | §6 |
| `validate_report.json` | 未来函数抽检、参数邻域、波动匹配、换月污染与剔除检验、回撤与集中度 | §6 |
| `replication_cffex.json` | 外部复现：CFFEX 股指/国债 5 品种的 IS/OOS、三个成本档、换手 | §7B |
| `replication_2010_2015.json` | 时间维度复现：2010–2015 商品期货 16 品种 | §7C |
| `oi_variants.json` | 持仓数据的 5 种替代构造（含"反向门"与"仅持仓变化"） | §7D |
| `cost_real_fees.json` | 逐品种费率三档情景与统一 5bp 的对照 | §7E |

## 没带的东西，以及为什么

| 没带 | 原因 |
|---|---|
| `panel.parquet`（5.5MB） | 平台行情数据不适合直接再分发；用 `scripts/factor.py` 一次拉齐 |
| `signals.parquet`（10MB） | 由面板可完全重算，属中间产物 |
| `bt_positions.csv`（4MB） | 逐日逐品种权重，可由 `backtest.py` 重算 |
| `bt_conditional.csv`（1.8MB） | 逐调仓日条件收益原表；`extreme_cases.csv` 已是它的摘要 |

## 怎么核对

```powershell
# 只读方式：直接看 JSON / CSV，不需要平台账号
python -c "import json;d=json.load(open('results/bt_metrics.json',encoding='utf-8'));print(d['windows']['OOS']['delta']['sharpe_B1_minus_B0'])"

# 全量重算（需要平台凭据）
cd 开发产物
python scripts\run_pipeline.py
```

`bt_metrics.json` 的 `windows.OOS.delta.sharpe_B1_minus_B0` 应当等于报告里的 **−0.0281**；
`breakeven_cost_bps` 应当等于 **4.4**。若不一致，说明代码或数据已经变化，报告需要重出。
