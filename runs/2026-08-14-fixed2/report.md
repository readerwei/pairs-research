# Pairs research -- 2026-08-14-fixed2

- universe: 81 symbols ingested (of 82 requested), 20 groups
- in-sample: 2022-11-22 -> 2025-05-21
- out-of-sample: 2025-05-22 -> 2026-08-14

## Screen

3 pairs passed correlation + cointegration + half-life + hedge-ratio gates.

sym_a sym_b         group      corr   coint_p      beta  half_life    last_z
 AMZN  META  megacap_tech  0.655897  0.006965  0.564597  18.534837 -0.511821
  CVX   XOM       big_oil  0.825382  0.034136  0.341714  24.166849 -1.611943
 GOOG  META  megacap_tech  0.562151  0.020748  0.434720  24.214757 -0.747966

## Out-of-sample results

      pair               params  is_sharpe  oos_sharpe oos_return   oos_dd  round_trips                                                                                                                                          verdict
   CVX/XOM  lb=48 ez=1.5 xz=0.5       0.10        0.31     +1.62%   -5.31%           11                                                                                                                           OOS sharpe 0.31 < 0.50
 AMZN/META  lb=37 ez=2.0 xz=0.5       1.58        0.26     +2.89%  -13.32%            9                                                                                    OOS sharpe 0.26 < 0.50; kept only 17% of IS sharpe (need 50%)
 GOOG/META  lb=48 ez=2.0 xz=0.5      -0.51        0.21     +2.41%  -15.11%            7  OOS sharpe 0.21 < 0.50; OOS drawdown -15.1% worse than -15%; IS sharpe -0.51 is not positive -- OOS 0.21 is unsupported by the in-sample window

## Residual exposure to leg B (out-of-sample)

Share of each pair's P&L variance explained by leg B's own returns rather than by
the spread converging. Sizing is dollar-neutral 1:-1, so any hedge ratio away from 1.0 leaks directional exposure.

           corr_with_leg_b  r_squared  beta_on_leg_b  sessions
pair                                                          
AMZN/META            0.231      0.054          0.106      97.0
CVX/XOM              0.196      0.038          0.051     142.0
GOOG/META            0.610      0.372          0.341     117.0

A high r_squared does not make a pair invalid -- it means that much of what the
backtest scored was single-name direction, not the mean reversion being tested.

## Risk statistics (pyfolio, out-of-sample)

                     AMZN/META  CVX/XOM  GOOG/META
Annual return            0.023    0.013      0.020
Cumulative returns       0.029    0.016      0.024
Annual volatility        0.112    0.045      0.138
Sharpe ratio             0.261    0.310      0.210
Calmar ratio             0.176    0.247      0.129
Stability                0.225    0.169      0.028
Max drawdown            -0.133   -0.053     -0.151
Omega ratio              1.104    1.078      1.073
Sortino ratio            0.442    0.461      0.286
Skew                     2.411    0.365     -1.763
Kurtosis                28.913    4.967     43.278
Tail ratio               0.763    0.993      1.015
Daily value at risk     -0.014   -0.006     -0.017
Gross leverage           0.894    0.931      0.905
Daily turnover           0.094    0.078      0.066

Tear sheets: `tearsheets/*.png`. Read Skew, Kurtosis and Tail ratio against the round-trip count above --
those moments describe a handful of events, not a distribution, when a pair traded a few times.

## Verdict

Nothing cleared the promotion gate. No config is cleared for live capital today.

