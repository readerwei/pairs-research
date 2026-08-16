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

## Verdict

Nothing cleared the promotion gate. No config is cleared for live capital today.

