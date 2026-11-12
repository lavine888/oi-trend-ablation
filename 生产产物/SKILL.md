---
name: alpha-61-production
description: 当需要读取「持仓确认趋势」F61 的生产计算结果时使用此 skill。只读取已生成的 Parquet 结果，不在调用时重新拉行情或重算因子。
tags: [quant, alpha, production, future, trend, open-interest]
---

# 持仓确认趋势生产结果（F61）

## 适用场景

- 当交易 agent 需要查询最新交易日的持仓确认趋势信号时
- 当需要按品种取 `buy / sell / hold` 与强度排名时

## 结果文件

- 文件路径：`生产产物/数据库.parquet`（相对本 skill 根目录）
- 数据格式：Parquet
- 更新频率：每交易日结算后（`scripts/refresh_production.py` 由调度器触发）
- 交易日口径：**最新已结算交易日**（当日 `settlement` 未发布时不出信号，自动回退一日）
- 生成方式：`开发产物/scripts/refresh_production.py`，复用已验收的 `factor.py` 特征与权重逻辑；
  研究窗口（2016–2025）冻结不变，只增量拉取研究窗口之后的行情
- 生成清单：`生产产物/build_manifest.json`（`trade_date`、`study_window`、`production_window`、
  质量检查、来源面板哈希）
- 当前快照：`2026-11-12`，36 个品种（buy 5 / sell 15 / hold 16）

## 主键

- `trade_date`
- `symbol`
- `factor_id`

## 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `trade_date` | string | 交易日 `YYYY-MM-DD` |
| `asset_type` | string | 固定 `future` |
| `symbol` | string | 主力连续序列代码，如 `A_DOMINANT.DCE` |
| `factor_id` | string | 固定 `F61` |
| `factor_name` | string | 固定 `OIConfirmedTrend` |
| `factor_value` | float | 带门趋势强度；0 表示当日未通过持仓确认 |
| `score` | float | 趋势强度横截面分位 0–100 |
| `rank` | int | 强度降序排名，1 为最强 |
| `signal` | string | `buy` / `sell` / `hold` |
| `confidence` | float | 确认时的 `|ΔOI_5d|` 分位，未确认为 0 |
| `data_version` | string | `F61-<交易日>-<面板哈希前8位>` |
| `update_time` | string | 结果生成时间（Asia/Shanghai） |

## 读取规则

1. 读取 `数据库.parquet`，默认取 `trade_date` 最大的一行集合。
2. 若最新交易日无结果，可回退到最近有效交易日，但**必须同时说明数据日期**。
3. `signal = hold` 表示该品种当日不持仓；不要把 `hold` 解释为「看多但不加仓」。

## 禁止行为

- 不允许在 agent 调用时重新拉取原始行情。
- 不允许在 agent 调用时重新计算因子。
- 不允许手工修改 Parquet 结果。
- 不允许把 `factor_value` 解释为资金流入金额。
