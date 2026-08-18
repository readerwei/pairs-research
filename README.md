# Trading research

Two tracks, one discipline: parameter search on in-sample data only, one config
carried to the holdout, holdout scored once. Everything here is research —
nothing in this folder places an order.

- **Pairs** (daily bars) — cointegration screen → grid → out-of-sample gate
- **Momentum** (1-minute bars) — the Learn-Algorithmic-Trading Chapter 4
  strategies over the live yaml universe. See [Momentum track](#momentum-track).

## Pairs track

```
run_daily.sh          ingest -> screen -> backtest -> report
config.py             universe, windows, thresholds, risk limits
ingest_research.py    builds the `pairs_research` bundle from Alpaca
data.py               bundle -> DataFrame helpers
screen_pairs.py       correlation + cointegration + half-life screen (IS only)
strategy.py           the parameterised pairs algorithm (backtest AND live)
backtest.py           IS parameter grid, then one OOS test per pair
report.py             verdict + promoted.json + pyfolio tear sheets
pyfolio_compat.py     one-line patch making pyfolio 0.9.2 work on pandas 1.x
runs/YYYY-MM-DD/      one folder per pass
  candidates.csv        pairs that passed the screen
  grid_in_sample.csv    every config tried in-sample
  out_of_sample.csv     one scored config per pair
  perf/*.pkl            raw zipline perf frames from the OOS runs
  tearsheets/*.png      pyfolio returns tear sheets
  report.md             the verdict
  promoted.json         configs cleared for live capital (often empty)
```

`report.py` builds tear sheets from the saved perf frames rather than re-running
anything, so the out-of-sample window is still only ever simulated once. If
pyfolio is not installed the report still works and just skips that section.

## The one rule

**The out-of-sample window is never used to choose anything.**

A pass evaluates roughly 2,000 hypotheses: ~150 candidate pairs through the
screen, then ~15 parameter combinations for each survivor. At that search
intensity the best in-sample result is overfit essentially by construction —
with 150 pairs tested at p ≤ 0.05, about 8 will pass the cointegration gate on
noise alone.

So in-sample output is treated as a list of things worth testing, never as
evidence. For each pair exactly one config — the in-sample Sharpe winner, among
those with at least 6 round trips — is carried to out-of-sample and scored once.
Running the grid on the holdout and reporting the best number is the same as
having no holdout.

Default split: 1500 calendar days of history, trailing 30% held out.

## Running it

```bash
source /home/wei/anaconda3/bin/activate alpaca
export ZIPLINE_TRADER_CONFIG=/home/wei/Documents/zipline-yaml/zipline-trader.yaml
cd /home/wei/Documents/zipline/research

./run_daily.sh                      # the whole pass

python ingest_research.py --list    # what's in the bundle
python screen_pairs.py --all        # every pair with its stats, no thresholds
python backtest.py --is-only        # grid only, don't touch the holdout
python backtest.py --pair AMZN GOOG --lookback 60 --entry 2.5 --exit 0.5
python backtest.py --pair AMZN GOOG --lookback 60 --entry 2.5 --exit 0.5 --oos
python report.py --date 2026-08-14
```

A full pass is about 3.5 minutes on this box.

## Design decisions worth knowing before you edit

**Log prices, not levels.** The hedge ratio comes from OLS of `log(A)` on
`log(B)`. On levels the ratio absorbs the price difference between the names: a
$150 stock against a $40 one produces a hedge covering ~15% of the exposure, so
the "spread" is a directional bet on the expensive leg in pairs costume. In logs
beta is an elasticity and 1.0 means balanced. `MIN_BETA`/`MAX_BETA` reject pairs
where one leg dominates.

**Pairs only screened within economic groups** (`UNIVERSE_GROUPS`). Cointegration
tests on unrelated names pass often enough to matter and mean nothing.

**Half-life ranks the survivors, not the p-value.** Among pairs that already
cleared the cointegration gate, how fast the spread reverts decides whether
there are enough round trips for the edge to show up. A p-value of 0.001 on a
spread with a 200-session half-life is untradeable.

**Risk controls run in the backtest, not just live.** `set_max_leverage`, a
per-leg cap, a gross-drift rebalance, and a z-score stop. A backtest without
them reports returns the account could never have taken. The z-stop is the
important one: past `STOP_Z` the relationship has broken (merger, guidance cut,
index reconstitution) and mean reversion is no longer the right model — without
it the strategy doubles down into exactly the divergences that end pairs books.

**`set_max_leverage` is a hard abort**, so it sits at 1.15 against a 0.90 gross
target. A dollar-neutral position drifts past 1.0 whenever the short leg rises
while the long falls; that is ordinary, and killing the run over it would be a
false alarm. `GROSS_REBALANCE_AT` resizes back to target first.

**`exit_z = 0.0` is in the grid to be rejected.** It means "hold until the spread
returns exactly to its mean," which almost never triggers, so those runs show one
round trip and a large drawdown. Kept in the grid as a control rather than
quietly dropped.

## Promotion gate

`report.py` writes `promoted.json` with the configs cleared for live capital.
A config must clear all four:

| gate | threshold |
|---|---|
| OOS Sharpe | ≥ 0.50 |
| OOS round trips | ≥ 5 |
| OOS max drawdown | ≥ −15% |
| Sharpe retention | OOS ≥ 50% of IS |

An empty `promoted.json` is the normal outcome and means the day's research
found nothing worth risking money on. It is the point of the gate, not a bug.

## First pass — 2026-08-14

Screened 150 within-group pairs over 2022-11-17 → 2025-05-20; 4 passed. Grid on
in-sample, one config each carried to 2025-05-21 → 2026-08-14:

| pair | IS Sharpe | OOS Sharpe | OOS return | OOS DD | round trips |
|---|---|---|---|---|---|
| AMZN/GOOG | 1.41 | 1.57 | +8.66% | −3.16% | 3 |
| AAPL/GOOG | 0.81 | 0.19 | +1.13% | −5.62% | 4 |
| XLE/XLF | 0.83 | −0.14 | −1.50% | −13.80% | 10 |
| XLE/XLI | 1.05 | −0.62 | −5.94% | −11.92% | 13 |

Nothing promoted. Three of four decayed to nothing or worse out of sample —
which is the expected result and the reason the split exists. AMZN/GOOG is the
only one that held up, and it traded 3 times in 15 months; that Sharpe is not
distinguishable from luck yet. It is the pair to keep watching, not to fund.

## Momentum track

```
minute_bundle.py       read-only handle on the alpaca_api minute bars
momentum.py            the two Chapter 4 strategies as zipline algorithms
backtest_momentum.py   IS grid -> one OOS run per strategy
symbol_study.py        walk-forward symbol selection, with a drop-the-winner check
basket.py              score a hand-picked basket, with leave-one-out
report_momentum.py     results table, pyfolio stats, tear sheets
```

Ported from
[Learn-Algorithmic-Trading Chapter 4](https://github.com/PacktPublishing/Learn-Algorithmic-Trading/tree/master/Chapter4):
a double moving average crossover and a consecutive-bar counter. Both are
single-asset signal generators in the book; here each name gets a fixed slice of
gross and runs independently.

**Costs dominate at this frequency.** Naive momentum at the book's N=5 fires 174
times a session; at N=3 it paid $101,504 of commission on a $100,000 account.
The reports print transactions per session and commission as a share of capital
next to returns, because a strategy that wins before costs and loses after is
the normal outcome here.

**Pin the window.** `alpaca_api` is re-ingested every weeknight, so results move
under you: one extra session shifted an out-of-sample result from +4.41% to
+4.09% with no code change. Use `--start`/`--end`, and read `run_meta.json` in
each run folder for the command that reproduces it.

## Live execution

```
live_runner.py         the engine -- every strategy runs through this
live_strategies.py     registry: signal logic only
run_live.sh            15-line cron launcher, no logic
eod_summary.py         end-of-day orders, holdings, engine-vs-broker check
```

```bash
python live_runner.py --list
python live_runner.py --strategy naive_momentum --check
python live_runner.py --strategy pingpong --once --max-seconds 300
python live_runner.py --strategy naive_momentum --session
```

Strategies contribute three things: `build()`, a `status()` line for the
heartbeat, and defaults. Everything that can lose money by being wrong lives in
the runner and is identical for all of them:

- refuses any endpoint that is not Alpaca paper without `--allow-real-money`
- refuses to start if another runner is trading an overlapping symbol -- two
  live algorithms on one position do not race and settle, they undo each other
- pre-flight: account, clock, positions, open orders, symbol resolution, sizing
- watchdog, heartbeat, quoted-vs-fill price reporting, engine-vs-broker
  reconciliation, flatten-on-exit policy

`--session` supervises `--once` children in a subprocess so a crash in the
engine cannot take down the supervisor meant to notice it, and a hard timeout
can bound a hang the watchdog missed.

Session logic is in Python, not bash: the calendar decides whether today is a
session and when it ends, which is what makes half-days work (the day after
Thanksgiving closes at 13:00, and a hardcoded 16:00 would leave a live
algorithm running into three hours of closed market).

```
ZIPLINE_TRADER_CONFIG=/home/wei/Documents/zipline-yaml/zipline-trader.yaml
25 8 * * 1-5 /home/wei/Documents/zipline/research/run_live.sh --strategy naive_momentum
```

Adding a strategy is one class in `live_strategies.py` -- `build()`, `status()`,
defaults -- and it inherits every guard above.

Also worth noting: the correlation gate started at 0.70 and **nothing** in the
universe passed both it and cointegration — the highly-correlated pairs are not
cointegrated and the cointegrated ones are not that correlated. It sits at 0.55
now, with the holdout doing the rejecting instead.
