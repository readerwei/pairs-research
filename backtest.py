"""Run pairs backtests: the in-sample parameter grid, then one out-of-sample test.

    python backtest.py                  # grid on IS, then OOS for each pair's winner
    python backtest.py --is-only        # stop after the grid (don't touch OOS)
    python backtest.py --pair AMZN GOOG --lookback 60 --entry 2.0 --exit 0.5

The discipline this file exists to enforce:

  * The grid only ever runs on the in-sample window.
  * For each pair, exactly ONE parameter set -- the in-sample winner -- is
    carried to out-of-sample. Running the grid on OOS and reporting the best
    result is the same as having no holdout at all.
  * The out-of-sample number is the only one worth acting on, and it is scored
    once.

Selection uses in-sample Sharpe with a minimum round-trip count, not total
return. A pairs config that made its money in two trades has not demonstrated
anything repeatable.
"""
from __future__ import print_function

import argparse
import itertools
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
import data as rdata  # noqa: E402
import strategy  # noqa: E402

# In-sample round-trip floor, DERIVED from the out-of-sample one rather than
# picked. report.MIN_OOS_ROUND_TRIPS applies to the OOS window; this applies to
# the IS window, and the two are different lengths. A flat 6 meant a config
# scraping through IS (629 sessions) projected to ~3 round trips over OOS (311
# sessions) against a floor of 5 -- pre-destined to fail the gate it was being
# selected for. Scaling by the window ratio makes the two agree.
#
#   IS sessions / OOS sessions = (1 - OOS_FRACTION) / OOS_FRACTION
#
# The +1 is a margin: trade frequency decays out of sample more often than it
# rises, so matching the projection exactly still fails about half the time.
def _min_round_trips():
    import report
    ratio = (1.0 - config.OOS_FRACTION) / config.OOS_FRACTION
    return int(np.ceil(report.MIN_OOS_ROUND_TRIPS * ratio)) + 1


MIN_ROUND_TRIPS = _min_round_trips()      # 13 at OOS_FRACTION=0.30, floor 5

_BENCH = {}


def benchmark_returns(start, end):
    key = (str(start.date()), str(end.date()))
    if key not in _BENCH:
        px = rdata.close_prices(start, end, symbols=['SPY'])['SPY']
        _BENCH[key] = px.pct_change().fillna(0.0)
    return _BENCH[key]


def metrics(perf):
    """Summary stats computed from the perf frame, not from zipline's rolling
    columns -- perf.sharpe is a to-date rolling value whose last row happens to
    equal the full-period number only when the window covers everything."""
    rets = perf.returns.astype(float)
    n = len(rets)
    if n == 0:
        return {}
    total = float(perf.portfolio_value.iloc[-1] / perf.portfolio_value.iloc[0] - 1)
    years = n / 252.0
    cagr = (1.0 + total) ** (1.0 / years) - 1.0 if years > 0 and total > -1 else np.nan
    vol = float(rets.std() * np.sqrt(252))
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() else np.nan
    curve = perf.portfolio_value.astype(float)
    dd = float((curve / curve.cummax() - 1.0).min())
    n_tx = int(sum(len(t) for t in perf.transactions))
    gross = perf.gross.astype(float) if 'gross' in perf else pd.Series([np.nan])
    return {
        'sessions': n,
        'total_return': total,
        'cagr': cagr,
        'vol': vol,
        'sharpe': sharpe,
        'max_dd': dd,
        'transactions': n_tx,
        'round_trips': n_tx // 4,        # 2 legs in + 2 legs out
        'exposure': float((gross.abs() > 0.01).mean()),
        'max_gross': float(gross.max()),
        'end_value': float(curve.iloc[-1]),
    }


def run_one(sym_a, sym_b, lookback, entry_z, exit_z, start, end,
            capital_base=None):
    """One backtest. Returns (metrics dict, perf frame)."""
    from zipline import run_algorithm

    init, handle, bts = strategy.make_algo(sym_a, sym_b, lookback, entry_z, exit_z)
    perf = run_algorithm(
        start=start, end=end,
        initialize=init, handle_data=handle, before_trading_start=bts,
        capital_base=capital_base or config.CAPITAL_BASE,
        benchmark_returns=benchmark_returns(start, end),
        bundle=config.BUNDLE,
        trading_calendar=config.nyse(),
        data_frequency='daily',
    )
    m = metrics(perf)
    m.update({'sym_a': sym_a, 'sym_b': sym_b, 'lookback': lookback,
              'entry_z': entry_z, 'exit_z': exit_z})
    return m, perf


def grid_for_pair(sym_a, sym_b, start, end):
    rows = []
    combos = list(itertools.product(config.GRID_LOOKBACK, config.GRID_ENTRY_Z,
                                    config.GRID_EXIT_Z))
    for lb, ez, xz in combos:
        if xz >= ez:
            continue                     # exit band must sit inside entry band
        try:
            m, _ = run_one(sym_a, sym_b, lb, ez, xz, start, end)
        except Exception as e:
            print('    %s/%s lb=%d ez=%.1f xz=%.1f FAILED: %s'
                  % (sym_a, sym_b, lb, ez, xz, e))
            continue
        rows.append(m)
        print('    lb=%-3d ez=%.1f xz=%.1f -> ret %+7.2f%%  sharpe %6.2f  '
              'dd %6.2f%%  rt %2d'
              % (lb, ez, xz, m['total_return'] * 100, m['sharpe'],
                 m['max_dd'] * 100, m['round_trips']))
    return pd.DataFrame(rows)


def pick_winner(df):
    """In-sample winner: best Sharpe among configs with enough round trips."""
    if df.empty:
        return None
    liquid = df[df.round_trips >= MIN_ROUND_TRIPS]
    if liquid.empty:
        return None
    return liquid.sort_values('sharpe', ascending=False).iloc[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--is-only', action='store_true',
                    help='run the in-sample grid and stop')
    ap.add_argument('--pair', nargs=2, metavar=('A', 'B'),
                    help='backtest a single pair instead of the screen output')
    ap.add_argument('--lookback', type=int, default=60)
    ap.add_argument('--entry', type=float, default=2.0)
    ap.add_argument('--exit', type=float, default=0.5)
    ap.add_argument('--oos', action='store_true',
                    help='with --pair: run on the out-of-sample window')
    args = ap.parse_args()

    start, end = config.session_range()
    is_start, is_end, oos_start, oos_end = config.split_sessions(start, end)
    out_dir = config.run_dir(args.date)

    if args.pair:
        a, b = args.pair
        s, e = (oos_start, oos_end) if args.oos else (is_start, is_end)
        print('%s/%s  lb=%d ez=%.1f xz=%.1f  %s  %s -> %s'
              % (a, b, args.lookback, args.entry, args.exit,
                 'OOS' if args.oos else 'IS', s.date(), e.date()))
        m, perf = run_one(a, b, args.lookback, args.entry, args.exit, s, e)
        for k in ('sessions', 'total_return', 'cagr', 'sharpe', 'max_dd',
                  'round_trips', 'exposure', 'end_value'):
            print('  %-14s %s' % (k, m[k]))
        return

    cand_path = os.path.join(out_dir, 'candidates.csv')
    if not os.path.exists(cand_path):
        print('no candidates.csv in %s -- run screen_pairs.py first' % out_dir)
        return
    cands = pd.read_csv(cand_path).head(config.TOP_N_PAIRS)
    print('IS  : %s -> %s' % (is_start.date(), is_end.date()))
    print('OOS : %s -> %s' % (oos_start.date(), oos_end.date()))
    print('pairs from screen: %d\n' % len(cands))

    all_is, winners = [], []
    for _, c in cands.iterrows():
        a, b = c['sym_a'], c['sym_b']
        print('  %s / %s  (%s)' % (a, b, c['group']))
        g = grid_for_pair(a, b, is_start, is_end)
        if g.empty:
            print('    no completed runs\n')
            continue
        g['group'] = c['group']
        all_is.append(g)
        w = pick_winner(g)
        if w is None:
            print('    no config reached %d round trips -- dropped\n'
                  % MIN_ROUND_TRIPS)
            continue
        print('    winner: lb=%d ez=%.1f xz=%.1f  IS sharpe %.2f\n'
              % (w.lookback, w.entry_z, w.exit_z, w.sharpe))
        winners.append(w)

    if all_is:
        is_df = pd.concat(all_is, ignore_index=True)
        is_df.to_csv(os.path.join(out_dir, 'grid_in_sample.csv'), index=False)
        print('written: %s' % os.path.join(out_dir, 'grid_in_sample.csv'))

    if args.is_only or not winners:
        return

    print('\n=== OUT OF SAMPLE (one config per pair, scored once) ===')
    perf_dir = os.path.join(out_dir, 'perf')
    if not os.path.isdir(perf_dir):
        os.makedirs(perf_dir)

    oos_rows = []
    for w in winners:
        try:
            m, perf = run_one(w.sym_a, w.sym_b, int(w.lookback), w.entry_z,
                              w.exit_z, oos_start, oos_end)
        except Exception as e:
            print('  %s/%s FAILED: %s' % (w.sym_a, w.sym_b, e))
            continue
        # Keep the perf frame so report.py can build tear sheets without
        # re-running the backtest -- and so the OOS run is scored exactly once,
        # which is the whole discipline here.
        perf.to_pickle(os.path.join(perf_dir, '%s_%s_oos.pkl'
                                    % (w.sym_a, w.sym_b)))
        m['is_sharpe'] = w.sharpe
        m['is_total_return'] = w.total_return
        oos_rows.append(m)
        print('  %-5s/%-5s lb=%-3d ez=%.1f xz=%.1f | IS sharpe %6.2f -> '
              'OOS sharpe %6.2f  ret %+7.2f%%  dd %6.2f%%  rt %2d'
              % (m['sym_a'], m['sym_b'], m['lookback'], m['entry_z'],
                 m['exit_z'], m['is_sharpe'], m['sharpe'],
                 m['total_return'] * 100, m['max_dd'] * 100, m['round_trips']))

    if oos_rows:
        oos = pd.DataFrame(oos_rows).sort_values('sharpe', ascending=False)
        path = os.path.join(out_dir, 'out_of_sample.csv')
        oos.to_csv(path, index=False)
        print('\nwritten: %s' % path)


if __name__ == '__main__':
    main()
