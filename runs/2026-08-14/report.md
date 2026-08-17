# Pairs research -- 2026-08-14

- universe: 81 symbols ingested (of 82 requested), 20 groups
- in-sample: 2022-11-17 -> 2025-05-20
- out-of-sample: 2025-05-21 -> 2026-08-14

## Screen

5 pairs passed correlation + cointegration + half-life + hedge-ratio gates.

sym_a sym_b         group      corr   coint_p      beta  half_life    last_z
 AMZN  GOOG  megacap_tech  0.625336  0.011429  1.229362  16.430103  1.501518
  XLE   XLI    sector_etf  0.597133  0.014686  0.356207  17.479527 -1.907694
 AMZN  META  megacap_tech  0.656268  0.010224  0.561591  18.532013 -0.281411
  CVX   XOM       big_oil  0.825344  0.042344  0.339391  23.585973 -1.439041
  JPM   WFC  money_center  0.760948  0.040809  1.008428  24.821436  0.401588

## Out-of-sample results

      pair               params  is_sharpe  oos_sharpe oos_return   oos_dd  round_trips                                                          verdict
 AMZN/GOOG  lb=60 ez=2.5 xz=0.5       1.41        1.57     +8.66%   -3.16%            3                                  only 3 OOS round trips (need 5)
 AMZN/META  lb=30 ez=2.0 xz=0.5       1.69        0.86    +11.31%  -11.94%           14                                                          PROMOTE
   CVX/XOM  lb=30 ez=2.0 xz=0.5       0.84        0.48     +2.02%   -2.62%           11                                           OOS sharpe 0.48 < 0.50
   JPM/WFC  lb=60 ez=2.0 xz=0.5       0.89        0.38     +2.69%   -5.54%            8    OOS sharpe 0.38 < 0.50; kept only 43% of IS sharpe (need 50%)
   XLE/XLI  lb=30 ez=2.0 xz=0.5       1.05       -0.62     -5.94%  -11.92%           13  OOS sharpe -0.62 < 0.50; kept only -59% of IS sharpe (need 50%)

## Risk statistics (pyfolio, out-of-sample)

                     AAPL/GOOG  AMZN/GOOG  AMZN/META  CVX/XOM  JPM/WFC  XLE/XLF  XLE/XLI
Annual return            0.009      0.069      0.090    0.016    0.022   -0.012   -0.048
Cumulative returns       0.011      0.087      0.113    0.020    0.027   -0.015   -0.059
Annual volatility        0.056      0.043      0.107    0.035    0.061    0.070    0.076
Sharpe ratio             0.191      1.569      0.861    0.476    0.384   -0.138   -0.617
Calmar ratio             0.163      2.192      0.757    0.622    0.391   -0.088   -0.405
Stability                0.047      0.917      0.049    0.060    0.085    0.304    0.474
Max drawdown            -0.056     -0.032     -0.119   -0.026   -0.055   -0.138   -0.119
Omega ratio              1.083      1.982      1.362    1.147    1.118    0.956    0.835
Sortino ratio            0.276      2.942      1.561    0.688    0.638   -0.200   -0.885
Skew                    -0.139      1.898      2.880    0.071    3.111    0.187    0.699
Kurtosis                31.587     15.021     32.121    8.275   42.697    8.972   10.540
Tail ratio               0.965      8.734      1.080    1.145    1.118    0.948    0.911
Daily value at risk     -0.007     -0.005     -0.013   -0.004   -0.008   -0.009   -0.010
Gross leverage           0.916      0.888      0.900    0.907    0.907    0.921    0.905
Daily turnover           0.082      0.108      0.120    0.106    0.066    0.122    0.120

Tear sheets: `tearsheets/*.png`. Read Skew, Kurtosis and Tail ratio against the round-trip count above --
those moments describe a handful of events, not a distribution, when a pair traded a few times.

## Verdict

1 config(s) cleared the gate and are written to promoted.json:

- AMZN/META lb=30 ez=2.0 xz=0.5 (OOS sharpe 0.86)

