"""Backtest the Chapter 4 momentum strategies on 1-minute bars.

    python backtest_momentum.py                 # IS grid, then one OOS run each
    python backtest_momentum.py --is-only
    python backtest_momentum.py --measure       # time a short run, then stop
    python backtest_momentum.py --one double_ma --params 20 100 --oos

Same discipline as the pairs work: the parameter grid only ever sees the
in-sample sessions, exactly one config per strategy is carried to the holdout,
and the holdout is scored once. Results land in runs/<date>-momentum/.

Minute bars change what matters. Turnover and commission dominate, so this
reports transactions and cost drag next to returns -- a strategy that beats the
market before costs and loses after is the normal outcome at this frequency, and
the numbers should make that visible rather than hide it in a Sharpe.
"""
from __future__ import print_function

import argparse
import os
import sys
import time
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
import minute_bundle  # noqa: E402
import momentum  # noqa: E402
from backtest import metrics as base_metrics  # noqa: E402

OOS_FRACTION = 0.30
WARMUP_SESSIONS = 2          # so a 200-bar lookback has history on bar one
CAPITAL_BASE = 100000

GRID = {
    'double_ma': [(10, 50), (20, 100), (50, 200)],   # book uses 20/100
    'naive_momentum': [(3,), (5,), (8,)],            # book uses 5
}


def sessions(cal=None):
    """(bundle, sessions that actually have MINUTE bars).

    `alpaca_api` carries four years of daily bars but only about one year of
    minute bars, so the daily reader's range is the wrong thing to split on --
    it would put the entire in-sample window in a period with no minute data.
    """
    cal = cal or config.nyse()
    data = minute_bundle.load()
    return data, minute_bundle.minute_sessions(data, cal)


def split(sess):
    cut = int(len(sess) * (1.0 - OOS_FRACTION))
    return sess[WARMUP_SESSIONS], sess[cut - 1], sess[cut], sess[-1]


_BENCH = {}


def benchmark(data, cal, start, end):
    key = (str(start.date()), str(end.date()))
    if key in _BENCH:
        return _BENCH[key]
    from zipline.data.data_portal import DataPortal
    dp = DataPortal(
        data.asset_finder, trading_calendar=cal,
        first_trading_day=data.equity_daily_bar_reader.first_trading_day,
        equity_daily_reader=data.equity_daily_bar_reader,
        adjustment_reader=data.adjustment_reader)
    sess = cal.sessions_in_range(start, end)
    spy = data.asset_finder.lookup_symbol('SPY', sess[-1])
    px = dp.get_history_window([spy], sess[-1], len(sess), '1d', 'close',
                               'daily').iloc[:, 0]
    _BENCH[key] = px.pct_change().fillna(0.0)
    return _BENCH[key]


def momentum_metrics(perf):
    m = base_metrics(perf)
    m.pop('round_trips', None)               # a pairs notion, meaningless here
    n_tx = m['transactions']
    # Commission lives on the order, not the transaction (perf.transactions
    # carries a `commission` key but this zipline leaves it None). The same
    # order id reappears in perf.orders on every day it stays open, so dedupe
    # by id and keep the largest value rather than summing the rows.
    by_id = {}
    for day in perf.orders:
        for o in day:
            c = o.get('commission') or 0.0
            if c > by_id.get(o['id'], 0.0):
                by_id[o['id']] = c
    m['commission'] = float(sum(by_id.values()))
    m['cost_drag'] = m['commission'] / CAPITAL_BASE
    m['tx_per_session'] = n_tx / float(m['sessions']) if m['sessions'] else 0.0
    m['avg_n_long'] = float(perf.n_long.mean()) if 'n_long' in perf else np.nan
    return m


def run_one(strategy, params, start, end, data=None, cal=None):
    from zipline import run_algorithm
    cal = cal or config.nyse()
    if data is None:
        data, _ = sessions(cal)

    factory = momentum.STRATEGIES[strategy]
    init, handle, bts = factory(minute_bundle.UNIVERSE, *params)

    t0 = time.time()
    perf = run_algorithm(
        start=start, end=end,
        initialize=init, handle_data=handle, before_trading_start=bts,
        capital_base=CAPITAL_BASE,
        benchmark_returns=benchmark(data, cal, start, end),
        bundle=minute_bundle.BUNDLE,
        trading_calendar=cal,
        data_frequency='minute',
    )
    m = momentum_metrics(perf)
    m.update({'strategy': strategy, 'params': '/'.join(str(p) for p in params),
              'elapsed_s': round(time.time() - t0, 1)})
    return m, perf


def fmt(m):
    return ('ret %+7.2f%%  sharpe %6.2f  dd %6.2f%%  tx %6d (%5.1f/session)  '
            'comm $%7.0f (%.2f%% of capital)'
            % (m['total_return'] * 100, m['sharpe'], m['max_dd'] * 100,
               m['transactions'], m['tx_per_session'], m['commission'],
               m['cost_drag'] * 100))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--is-only', action='store_true')
    ap.add_argument('--measure', action='store_true',
                    help='time one 5-session run per strategy and stop')
    ap.add_argument('--one', choices=sorted(momentum.STRATEGIES))
    ap.add_argument('--params', nargs='+', type=int)
    ap.add_argument('--oos', action='store_true')
    args = ap.parse_args()

    cal = config.nyse()
    data, sess = sessions(cal)
    is_start, is_end, oos_start, oos_end = split(sess)
    print('bundle sessions : %d  (%s -> %s)'
          % (len(sess), sess[0].date(), sess[-1].date()))
    print('in-sample       : %s -> %s' % (is_start.date(), is_end.date()))
    print('out-of-sample   : %s -> %s' % (oos_start.date(), oos_end.date()))

    out_dir = config.run_dir(str(sess[-1].date()) + '-momentum')

    if args.measure:
        for strat, grid in sorted(GRID.items()):
            m, _ = run_one(strat, grid[0], sess[WARMUP_SESSIONS],
                           sess[WARMUP_SESSIONS + 5], data, cal)
            per = m['elapsed_s'] / 6.0
            print('%-16s %.1fs for 6 sessions -> %.1fs/session, IS grid est '
                  '%.0f min' % (strat, m['elapsed_s'], per,
                                per * (len(sess) * 0.7) * len(grid) / 60))
        return

    if args.one:
        s, e = (oos_start, oos_end) if args.oos else (is_start, is_end)
        m, perf = run_one(args.one, tuple(args.params), s, e, data, cal)
        print('\n%s %s  %s' % (args.one, m['params'],
                               'OOS' if args.oos else 'IS'))
        print('  ' + fmt(m))
        return

    rows, winners = [], []
    for strat in sorted(GRID):
        print('\n=== %s (in-sample) ===' % strat)
        strat_rows = []
        for params in GRID[strat]:
            m, _ = run_one(strat, params, is_start, is_end, data, cal)
            strat_rows.append(m)
            rows.append(m)
            print('  %-9s %s' % (m['params'], fmt(m)))
        best = sorted(strat_rows, key=lambda r: r['sharpe'], reverse=True)[0]
        print('  winner: %s (IS sharpe %.2f)' % (best['params'], best['sharpe']))
        winners.append(best)

    pd.DataFrame(rows).to_csv(os.path.join(out_dir, 'grid_in_sample.csv'),
                              index=False)
    print('\nwritten: %s' % os.path.join(out_dir, 'grid_in_sample.csv'))
    if args.is_only:
        return

    print('\n=== OUT OF SAMPLE (one config per strategy, scored once) ===')
    perf_dir = os.path.join(out_dir, 'perf')
    if not os.path.isdir(perf_dir):
        os.makedirs(perf_dir)

    oos_rows = []
    for w in winners:
        params = tuple(int(p) for p in w['params'].split('/'))
        m, perf = run_one(w['strategy'], params, oos_start, oos_end, data, cal)
        m['is_sharpe'] = w['sharpe']
        m['is_total_return'] = w['total_return']
        oos_rows.append(m)
        perf.to_pickle(os.path.join(perf_dir, '%s_%s_oos.pkl'
                                    % (w['strategy'], w['params'].replace('/', '-'))))
        print('  %-16s %-9s %s' % (m['strategy'], m['params'], fmt(m)))
        print('  %-16s   IS sharpe %.2f -> OOS sharpe %.2f'
              % ('', m['is_sharpe'], m['sharpe']))

    pd.DataFrame(oos_rows).to_csv(os.path.join(out_dir, 'out_of_sample.csv'),
                                  index=False)
    print('\nwritten: %s' % os.path.join(out_dir, 'out_of_sample.csv'))


if __name__ == '__main__':
    main()
