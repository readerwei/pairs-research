# Minute-bar momentum research -- 2026-08-14-momentum

Strategies ported from Learn-Algorithmic-Trading Chapter 4:
`ch4_double_moving_average.py` and `ch4_naive_momentum_strategy2.py`.
Universe: the 33 tradeable names from the live yaml `custom_asset_list`.
Bars: 1-minute. Long-only, fixed slice per name, commission $0.005/share ($1 min) plus volume-share slippage.

## In-sample parameter grid

       strategy  params  total_return   sharpe  max_dd  transactions  tx_per_session  commission  cost_drag  avg_n_long
      double_ma   10/50       -0.5981 -12.3077 -0.6038         67763        391.6936   63473.570     0.6347     16.2948
      double_ma  20/100       -0.2890  -5.0658 -0.3091         34015        196.6185   31723.305     0.3172     15.7803
      double_ma  50/200       -0.1203  -1.9741 -0.1803         15869         91.7283   14810.315     0.1481     16.5029
 naive_momentum       3       -0.9981 -16.0113 -0.9981        105123        607.6474  101503.860     1.0150     16.7861
 naive_momentum       5       -0.2553  -4.7580 -0.2777         30147        174.2601   29036.760     0.2904     16.7803
 naive_momentum       8        0.0241   0.4170 -0.0953          3319         19.1850    3221.110     0.0322     16.8844

## Out-of-sample (scored once)

       strategy  params  total_return  sharpe  max_dd  transactions  tx_per_session  commission  cost_drag  avg_n_long  is_sharpe
      double_ma  50/200       -0.0345 -1.1290  -0.063          6999         93.3200    6689.595     0.0669     15.8400    -1.9741
 naive_momentum       8        0.0441  1.9048  -0.022          1427         19.0267    1406.025     0.0141     16.4267     0.4170

## Risk statistics (pyfolio, out-of-sample)

                     double/ma/50-200  naive/momentum/8
Annual return                  -0.092             0.163
Cumulative returns             -0.028             0.046
Annual volatility               0.083             0.081
Sharpe ratio                   -1.129             1.905
Calmar ratio                   -1.467             7.415
Stability                       0.758             0.646
Max drawdown                   -0.063            -0.022
Omega ratio                     0.839             1.354
Sortino ratio                  -1.520             3.032
Skew                            0.052             0.045
Kurtosis                       -0.684            -0.145
Tail ratio                      0.891             1.202
Daily value at risk            -0.011            -0.010
Gross leverage                  0.442             0.450
Daily turnover                  5.319             1.165

Tear sheets: `tearsheets/*.png`

