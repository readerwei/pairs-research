# Pairs research -- 2026-08-20

- universe: 81 symbols ingested (of 82 requested), 20 groups
- in-sample: 2022-11-22 -> 2025-05-21
- out-of-sample: 2025-05-22 -> 2026-08-14

## Screen

3 pairs passed correlation + cointegration + half-life + hedge-ratio gates.

sym_a sym_b         group      corr   coint_p      beta  half_life    last_z
 AMZN  GOOG  megacap_tech  0.621542  0.006281  1.228841  15.812585  0.746380
  XLE   XLI    sector_etf  0.600176  0.014495  0.357411  17.905212 -2.179773
  COP   CVX       big_oil  0.784563  0.034384  0.535915  59.054696 -1.769875

## Out-of-sample results

      pair               params  is_sharpe  oos_sharpe oos_return  oos_dd  round_trips                                                          verdict
   COP/CVX  lb=90 ez=2.0 xz=0.5       0.02        1.16     +6.94%  -2.37%            4                                  only 4 OOS round trips (need 5)
 AMZN/GOOG  lb=32 ez=2.5 xz=0.5       1.18        1.09     +6.62%  -3.18%            6                                                          PROMOTE
   XLE/XLI  lb=36 ez=2.0 xz=0.5       1.08       -0.13     -1.57%  -9.85%            8  OOS sharpe -0.13 < 0.50; kept only -12% of IS sharpe (need 50%)

## Residual exposure to leg B (out-of-sample)

Share of each pair's P&L variance explained by leg B's own returns rather than by
the spread converging. Sizing is dollar-neutral 1:-1, so any hedge ratio away from 1.0 leaks directional exposure.

           corr_with_leg_b  r_squared  beta_on_leg_b  sessions
pair                                                          
AMZN/GOOG            0.199      0.039          0.073      60.0
COP/CVX             -0.053      0.003         -0.019      93.0
XLE/XLI              0.654      0.428          0.568      87.0

A high r_squared does not make a pair invalid -- it means that much of what the
backtest scored was single-name direction, not the mean reversion being tested.

## Risk statistics (pyfolio, out-of-sample)

                     AMZN/GOOG  COP/CVX  XLE/XLI
Annual return            0.053    0.056   -0.013
Cumulative returns       0.066    0.069   -0.016
Annual volatility        0.049    0.048    0.077
Sharpe ratio             1.086    1.165   -0.127
Calmar ratio             1.678    2.363   -0.129
Stability                0.907    0.867    0.130
Max drawdown            -0.032   -0.024   -0.099
Omega ratio              1.469    1.412    0.960
Sortino ratio            1.810    1.881   -0.184
Skew                     1.045    0.601    0.449
Kurtosis                 8.931    6.556   11.190
Tail ratio               1.370    1.259    1.140
Daily value at risk     -0.006   -0.006   -0.010
Gross leverage           0.904    0.894    0.910
Daily turnover           0.122    0.051    0.105

Tear sheets: `tearsheets/*.png`. Read Skew, Kurtosis and Tail ratio against the round-trip count above --
those moments describe a handful of events, not a distribution, when a pair traded a few times.

## Verdict

1 config(s) cleared the gate and are written to promoted.json:

- AMZN/GOOG lb=32 ez=2.5 xz=0.5 (OOS sharpe 1.09)

