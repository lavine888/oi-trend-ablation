---
name: alpha-61
description: 当需要开发、计算、验证「持仓确认趋势」因子时使用此 skill。它在纯价格趋势基线上叠加一道持仓量确认门，用于判断持仓变化这一数据来源是否带来可扣费的增量信息。
tags: [quant, alpha, development, future, trend, open-interest]
---

# 持仓确认趋势 Alpha（F61 / OIConfirmedTrend）

## 适用场景

- 当需要判断「期货持仓变化」是否值得作为独立数据源接入趋势策略时
- 当需要复现「价格趋势 vs 价格趋势+持仓确认」的来源消融对比时
- 当需要给交易 agent 提供逐品种的持有/回避信号时

## 因子逻辑

- **核心假设**：价格趋势若无持仓量同向扩张配合，更可能是平仓推动的短期波动而非新资金建立的趋势；有持仓扩张确认的趋势更可能延续。
- **计算公式**
  - 日收益 `r_t = settlement_t / pre_settlement_t − 1`（同合约结算价，避免换月跳空）
  - 趋势 `mom60_t = Σ_{k=0..59} log(1 + r_{t−k})`
  - 持仓变化 `oi_chg5_t = OI_t / OI_{t−5} − 1`
  - 基线权重 `w_B0 = sign(mom60) × 1/N`
  - 处理权重 `w_B1 = w_B0 × 1[sign(oi_chg5) = sign(mom60)]`
  - 因子值 `factor_value = sign(mom60) × |mom60| × 1[gate]`（未确认置 0）
- **排序方向**：正的 `factor_value` 偏多，负的偏空，0 表示观望。
- **适用市场**：境内商品期货主力连续合约（上期所/大商所/郑商所/能源中心/广期所）。

## 输入数据

因子计算必须使用 PandaAI data 数据拉取库；本 skill 只用 `panda_data.get_future_daily`。

| 字段 | 说明 | 来源 |
|---|---|---|
| `date` | 交易日 | `get_future_daily` |
| `symbol` | 主力连续序列，如 `A_DOMINANT.DCE` | `get_future_daily` |
| `settlement` / `pre_settlement` | 结算价 / 昨结算价（同合约） | `get_future_daily` |
| `open_interest` | 交易所累计持仓量（唯一增量来源） | `get_future_daily` |
| `volume` | 成交量（仅安慰剂对照） | `get_future_daily` |
| `amount` | 成交额（流动性过滤） | `get_future_daily` |

数据口径、接口限制与已知边界见 `references/data_guide.md`。

## 输出结果

| 字段 | 说明 |
|---|---|
| `factor_value` | 带门的趋势强度（未确认 = 0） |
| `score` | 趋势强度横截面分位，0–100 |
| `rank` | 强度降序排名 |
| `signal` | `buy` / `sell` / `hold`（`hold` = 未通过持仓确认） |
| `confidence` | 通过确认时为 `|oi_chg5|` 的横截面分位，否则 0 |

## 使用方式

```bash
python scripts/factor.py                 # 拉平台数据 + 建面板 + 出信号
python scripts/backtest.py               # 冻结基线 vs 处理组 vs 安慰剂，扣费回测
python scripts/validate.py               # 未来函数 / 参数邻域 / 置换检验 / 极值案例 / 风险
python scripts/build_production.py       # 落 数据库.parquet
python scripts/selftest_engine.py        # 引擎自检（无平台依赖）
```

## 验收要求

- 不允许未来函数：信号 `t` 日收盘确定，收益 `t+1` 起算；`validate.py` 抽样重算 + 偷看一日负向对照。
- 必须有样本外验证：IS 2016–2021 定宇宙，OOS 2022–2025 只评估一次。
- 必须有回测指标：IC/ICIR、分层、多空、最大回撤、换手、样本外表现、信号样例。
- 必须使用 PandaAI data 数据拉取库实现。
- 不通过验证不得进入生产。

## 口径红线

- 持仓量是**交易所汇总未平仓合约数**，只是持仓变化代理；**不得**表述为净资金流入、主力净多或资金方向。
- 成交量不是本期增量来源，只作安慰剂；新闻来源因缺发布时间字段本期排除。
- 不得把多个已有 CTA 因子加权平均当成新增研究。
