<div align="center">

# OI Trend Ablation

**Lavine Version · 量枢院 Alpha-61**

Does open-interest change confirm a futures trend?

A pre-registered, one-command-reproducible **negative result**: **no.**

[**简体中文**](README.md) · English

[![CI](https://github.com/lavine888/oi-trend-ablation/actions/workflows/ci.yml/badge.svg)](https://github.com/lavine888/oi-trend-ablation/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-3776ab)
![Data](https://img.shields.io/badge/data-PandaData-e11d48)
![Result](https://img.shields.io/badge/result-negative-dc2626)
[![License](https://img.shields.io/badge/license-GPL--3.0-334155)](LICENSE)

</div>

---

## The one-paragraph result

Take a plain price-trend CTA baseline on 36 Chinese commodity futures (dominant continuous
contracts, 2016–2025), add **exactly one** extra data source — a gate that requires open
interest to expand in the same direction as the trend — and evaluate out-of-sample (2022–2025)
net of 5bp per side:

| OOS metric | Baseline B0 | B1 (+ OI gate) |
|---|---:|---:|
| Sharpe | −0.005 | **−0.033** |
| Volatility | 7.2% | 4.1% |
| Max drawdown | −17.7% | −10.1% |
| Annual turnover | 13.6 | **26.7** |
| **ΔSharpe** | — | **−0.028** |

Break-even cost is **4.4bp per side**, below realistic Chinese futures friction
(commission + one tick ≈ 5bp+). The gate only looks good in a near-zero-cost world.

Volatility and drawdown *do* improve — but that is just exposure halving: scale the baseline by
0.56 and you get the same risk profile without paying twice the turnover.

**Replication:** the same parameters moved to CFFEX index and treasury futures (IC/IF/IH/TF/T,
same split, same 5bp) give **ΔSharpe = −0.165**, negative at 2bp/5bp/10bp alike, with the same
turnover doubling.

## Four independent judgements

| Judgement | Result | Reading |
|---|---|---|
| Cross-sectional IC (auxiliary) | gate 0.0043; gated factor 0.0015, **below** raw momentum 0.0073 | the gate weakens the signal |
| Conditional returns (primary) | confirmed +0.140% vs unconfirmed +0.177% (t = −0.78) | hypothesis points the wrong way |
| Permutation test | 500 random gates; observed −0.028 inside null distribution (p = 0.11, 95th pct +0.089) | indistinguishable from "trade less" |
| Parameter neighbourhood | 4×3 grid, only 3/12 cells positive, max +0.093 | not a tuning problem |

## Method

| ID | Definition | Role |
|---|---|---|
| **B0** | `w = sign(mom60) × 1/N`, 5-day rebalance, net of cost | frozen baseline: price trend only |
| **B1** | `w = B0 × 1[sign(ΔOI_5d) = sign(mom60)]` | treatment: one gate, nothing else |
| **P1** | same gate but on volume change | placebo |
| **P2** | permuted gate labels, 500 draws | null distribution |
| **P3** | B1 rescaled to B0 volatility | rules out the low-exposure explanation |

Returns use `settlement / pre_settlement − 1` (same contract, no roll jump). IS 2016–2021 fixes
the universe; OOS 2022–2025 is evaluated once. Costs `Σ|Δw| × c`, primary 5bp.

## Quick start

```powershell
git clone https://github.com/lavine888/oi-trend-ablation.git
Set-Location oi-trend-ablation
python -m pip install -r requirements.txt

$env:PANDA_DATA_USERNAME = "your-account"
$env:PANDA_DATA_PASSWORD = "your-password"

Set-Location 开发产物
python scripts\run_pipeline.py                 # fetch -> panel -> backtest -> validate -> production -> report -> gate
python scripts\replicate_cffex.py              # external replication on CFFEX
```

No account? Everything in `results/` is shipped so you can check every number in the report
without touching the platform; `scripts\selftest_engine.py` verifies the engine on synthetic data.

## Layout

```text
00_预注册_研究设计与口径.md   pre-registered design (frozen, sha256-recorded)
研究报告.md                  full report (Chinese): results, extreme cases, risks, value
开发产物/                    dev deliverables: SKILL.md, scripts/, references/
生产产物/                    production: SKILL.md, database.parquet, build manifest
results/                    evidence bundle behind every number in the report
```

## Why publish a negative result

Because they get buried, and everyone pays the same tuition again. This repo keeps the whole
trail: pre-registration frozen before any performance, one-variable ablation, four independent
judgements, quantified roll contamination, and a one-command reproduction.

## Caveats

- Open interest is an exchange-wide aggregate: it cannot sign long vs short, and is **not**
  capital flow. Nothing here claims net inflow.
- Costs are a uniform basis-point model; per-variety fees and tick sizes are not modelled.
- The baseline itself fails out-of-sample (IS 0.625 → OOS −0.005), so this is "no evidence of
  an edge", not "proof of a negative edge".
- The OOS window was evaluated four times in practice (format/engineering/data defects);
  each is logged in report §10.

## Disclaimer

Research and education only. Not affiliated with PandaAI, 量枢院 or any exchange. Historical
backtest, **not investment advice**.

## License

[GNU General Public License v3.0](LICENSE)
