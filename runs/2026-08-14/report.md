# Pairs research -- 2026-08-14

- universe: 81 symbols ingested (of 82 requested), 20 groups
- in-sample: 2022-11-17 -> 2025-05-20
- out-of-sample: 2025-05-21 -> 2026-08-14

## Screen

4 pairs passed correlation + cointegration + half-life + hedge-ratio gates.

sym_a sym_b         group      corr   coint_p      beta  half_life    last_z
  XLE   XLI    sector_etf  0.586817  0.012771  0.432149  20.377055 -1.850626
 AMZN  GOOG  megacap_tech  0.649831  0.017621  1.157325  20.830963  1.318837
  XLE   XLF    sector_etf  0.584857  0.023788  0.355235  22.412684 -1.558923
 AAPL  GOOG  megacap_tech  0.561834  0.035326  0.676595  27.061885 -0.156906

## Out-of-sample results

      pair               params  is_sharpe  oos_sharpe oos_return   oos_dd  round_trips                                                                                         verdict
 AMZN/GOOG  lb=60 ez=2.5 xz=0.5       1.41        1.57     +8.66%   -3.16%            3                                                                 only 3 OOS round trips (need 5)
 AAPL/GOOG  lb=60 ez=2.5 xz=0.5       0.81        0.19     +1.13%   -5.62%            4  OOS sharpe 0.19 < 0.50; only 4 OOS round trips (need 5); kept only 24% of IS sharpe (need 50%)
   XLE/XLF  lb=30 ez=2.0 xz=0.5       0.83       -0.14     -1.50%  -13.80%           10                                 OOS sharpe -0.14 < 0.50; kept only -17% of IS sharpe (need 50%)
   XLE/XLI  lb=30 ez=2.0 xz=0.5       1.05       -0.62     -5.94%  -11.92%           13                                 OOS sharpe -0.62 < 0.50; kept only -59% of IS sharpe (need 50%)

## Risk statistics (pyfolio, out-of-sample)

                     AAPL/GOOG  AMZN/GOOG  XLE/XLF  XLE/XLI
Annual return            0.009      0.069   -0.012   -0.048
Cumulative returns       0.011      0.087   -0.015   -0.059
Annual volatility        0.056      0.043    0.070    0.076
Sharpe ratio             0.191      1.569   -0.138   -0.617
Calmar ratio             0.163      2.192   -0.088   -0.405
Stability                0.047      0.917    0.304    0.474
Max drawdown            -0.056     -0.032   -0.138   -0.119
Omega ratio              1.083      1.982    0.956    0.835
Sortino ratio            0.276      2.942   -0.200   -0.885
Skew                    -0.139      1.898    0.187    0.699
Kurtosis                31.587     15.021    8.972   10.540
Tail ratio               0.965      8.734    0.948    0.911
Daily value at risk     -0.007     -0.005   -0.009   -0.010
Gross leverage           0.916      0.888    0.921    0.905
Daily turnover           0.082      0.108    0.122    0.120

Tear sheets: `tearsheets/*.png`. Read Skew, Kurtosis and Tail ratio against the round-trip count above --
those moments describe a handful of events, not a distribution, when a pair traded a few times.

## Verdict

Nothing cleared the promotion gate. No config is cleared for live capital today.

