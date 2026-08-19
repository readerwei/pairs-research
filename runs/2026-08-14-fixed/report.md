# Pairs research -- 2026-08-14-fixed

- universe: 81 symbols ingested (of 82 requested), 20 groups
- in-sample: 2022-11-21 -> 2025-05-21
- out-of-sample: 2025-05-22 -> 2026-08-14

## Screen

3 pairs passed correlation + cointegration + half-life + hedge-ratio gates.

sym_a sym_b         group      corr   coint_p      beta  half_life    last_z
 AMZN  META  megacap_tech  0.655897  0.006965  0.564597  18.534837 -0.511821
  CVX   XOM       big_oil  0.825382  0.034136  0.341714  24.166849 -1.611943
 GOOG  META  megacap_tech  0.562151  0.020748  0.434720  24.214757 -0.747966

## Out-of-sample results

      pair               params  is_sharpe  oos_sharpe oos_return   oos_dd  round_trips                                                                                               verdict
   CVX/XOM  lb=48 ez=1.5 xz=0.5       0.24        0.31     +1.62%   -5.31%           11                                                                                OOS sharpe 0.31 < 0.50
 AMZN/META  lb=37 ez=2.0 xz=0.5       1.38       -0.23     -5.07%  -17.01%            6  OOS sharpe -0.23 < 0.50; OOS drawdown -17.0% worse than -15%; kept only -17% of IS sharpe (need 50%)

## Residual exposure to leg B (out-of-sample)

Share of each pair's P&L variance explained by leg B's own returns rather than by
the spread converging. Sizing is dollar-neutral 1:-1, so any hedge ratio away from 1.0 leaks directional exposure.

           corr_with_leg_b  r_squared  beta_on_leg_b  sessions
pair                                                          
AMZN/META           -0.110      0.012         -0.053     168.0
CVX/XOM              0.196      0.038          0.051     142.0

A high r_squared does not make a pair invalid -- it means that much of what the
backtest scored was single-name direction, not the mean reversion being tested.

## Risk statistics (pyfolio, out-of-sample)

                     AMZN/META  CVX/XOM
Annual return           -0.041    0.013
Cumulative returns      -0.051    0.016
Annual volatility        0.139    0.045
Sharpe ratio            -0.233    0.310
Calmar ratio            -0.243    0.247
Stability                0.594    0.169
Max drawdown            -0.170   -0.053
Omega ratio              0.938    1.078
Sortino ratio           -0.333    0.461
Skew                     0.371    0.365
Kurtosis                16.344    4.967
Tail ratio               1.021    0.993
Daily value at risk     -0.018   -0.006
Gross leverage           0.946    0.931
Daily turnover           0.046    0.078

Tear sheets: `tearsheets/*.png`. Read Skew, Kurtosis and Tail ratio against the round-trip count above --
those moments describe a handful of events, not a distribution, when a pair traded a few times.

## Verdict

Nothing cleared the promotion gate. No config is cleared for live capital today.

