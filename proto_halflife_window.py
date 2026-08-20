"""PROTOTYPE -- z-score window from the estimated half-life, not the grid.

    python proto_halflife_window.py                # all screened pairs
    python proto_halflife_window.py --date 2026-08-14

The idea (Jansen, ML4T ch.9 nb06): the lookback that defines the z-score should
be a property of the PAIR, not a free parameter. A spread that reverts in 5
sessions and one that takes 40 do not want the same window. He uses

    window = 2 * half_life

and half_life already falls out of screen_pairs.pair_stats -- we compute it,
gate on it, then throw it away and grid-search `lookback` instead.

This script does not modify anything. For each pair it re-derives the in-sample
half-life, sets lookback = round(2*hl), and runs that config through the SAME
backtest.run_one used by the real pipeline -- IS and OOS -- so the numbers are
directly comparable with runs/<date>/grid_in_sample.csv and out_of_sample.csv.

The comparison is deliberately unfair in the grid's favour: the grid winner was
chosen as the best of ~15 in-sample configs, while the half-life window is
picked with ZERO search. If it holds up out of sample anyway, that is the whole
argument -- one less fitted parameter for the same result.

entry_z / exit_z are held FIXED at 2.0 / 0.5 (the modal grid winner) so that
lookback is the only thing that varies.
"""
from __future__ import print_function

import argparse
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest  # noqa: E402
import config  # noqa: E402
import data as rdata  # noqa: E402
import screen_pairs  # noqa: E402

FIXED_ENTRY_Z = 2.0
FIXED_EXIT_Z = 0.5

# A window still has to be long enough to estimate a slope on and short enough
# that WARMUP_SESSIONS covers it -- config gives up WARMUP_SESSIONS of history
# before the IS window starts, so a longer window would run off the front.
MIN_WINDOW = 10
MAX_WINDOW = config.WARMUP_SESSIONS - 5


def is_half_life(px, a, b):
    """In-sample half-life + beta of the log-price residual, as screen_pairs does."""
    sub = px[[a, b]].dropna()
    sub = sub[(sub > 0).all(axis=1)]
    ya = np.log(sub[a].values.astype(float))
    xb = np.log(sub[b].values.astype(float))
    beta, intercept = np.polyfit(xb, ya, 1)
    spread = ya - (beta * xb + intercept)
    return screen_pairs.half_life(spread), float(beta)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    args = ap.parse_args()

    # rdata.session_range(), NOT config.session_range(): config computes the
    # window from the exchange calendar, which knows about today, while the
    # bundle stops at its last ingest. When the two disagree -- pairs_research
    # ends 2026-08-14 but the calendar has opened 2026-08-18 -- zipline raises
    # a bare KeyError from inside the history loader on the OOS run.
    start, end = rdata.session_range()
    is_start, is_end, oos_start, oos_end = config.split_sessions(start, end)
    out_dir = config.run_dir(args.date)

    cand_path = os.path.join(out_dir, 'candidates.csv')
    if not os.path.exists(cand_path):
        print('no candidates.csv in %s' % out_dir)
        return
    cands = pd.read_csv(cand_path).head(config.TOP_N_PAIRS)

    # the grid winners actually used, for a like-for-like comparison
    grid_path = os.path.join(out_dir, 'grid_in_sample.csv')
    grid = pd.read_csv(grid_path) if os.path.exists(grid_path) else pd.DataFrame()
    oos_path = os.path.join(out_dir, 'out_of_sample.csv')
    prev_oos = pd.read_csv(oos_path) if os.path.exists(oos_path) else pd.DataFrame()

    print('IS  : %s -> %s' % (is_start.date(), is_end.date()))
    print('OOS : %s -> %s' % (oos_start.date(), oos_end.date()))
    print('window = 2 x in-sample half-life, clamped to [%d, %d]'
          % (MIN_WINDOW, MAX_WINDOW))
    print('entry_z / exit_z held fixed at %.1f / %.1f\n' % (FIXED_ENTRY_Z,
                                                            FIXED_EXIT_Z))

    px_is = rdata.close_prices(is_start, is_end)

    rows = []
    for _, c in cands.iterrows():
        a, b = c['sym_a'], c['sym_b']
        hl, beta = is_half_life(px_is, a, b)
        if not np.isfinite(hl):
            print('%s/%s: half-life not finite -- skipped' % (a, b))
            continue
        win = int(round(2 * hl))
        win = max(MIN_WINDOW, min(MAX_WINDOW, win))

        # what the grid picked for this pair, for reference
        gw = None
        if not grid.empty:
            g = grid[(grid.sym_a == a) & (grid.sym_b == b)]
            gw = backtest.pick_winner(g)

        print('%s/%s  half-life %.1f -> window %d   (grid picked lookback %s)'
              % (a, b, hl, win, int(gw.lookback) if gw is not None else 'n/a'))

        rec = {'sym_a': a, 'sym_b': b, 'group': c['group'],
               'half_life': hl, 'window': win, 'is_beta': beta,
               'grid_lookback': int(gw.lookback) if gw is not None else np.nan}

        for label, s, e in (('IS', is_start, is_end), ('OOS', oos_start, oos_end)):
            try:
                m, _ = backtest.run_one(a, b, win, FIXED_ENTRY_Z, FIXED_EXIT_Z,
                                        s, e)
            except Exception as exc:
                print('    %s FAILED: %s' % (label, exc))
                continue
            for k in ('sharpe', 'total_return', 'max_dd', 'round_trips',
                      'exposure'):
                rec['%s_%s' % (label.lower(), k)] = m[k]
            print('    %-3s sharpe %6.2f  ret %+7.2f%%  dd %6.2f%%  rt %2d  '
                  'exposure %5.1f%%'
                  % (label, m['sharpe'], m['total_return'] * 100,
                     m['max_dd'] * 100, m['round_trips'], m['exposure'] * 100))

        # the pipeline's own OOS number for this pair
        if not prev_oos.empty:
            p = prev_oos[(prev_oos.sym_a == a) & (prev_oos.sym_b == b)]
            if len(p):
                p = p.iloc[0]
                rec['grid_oos_sharpe'] = p['sharpe']
                rec['grid_oos_return'] = p['total_return']
                rec['grid_oos_rt'] = p['round_trips']
                rec['grid_oos_exposure'] = p['exposure']
                print('    vs grid OOS: sharpe %6.2f  ret %+7.2f%%  rt %2d  '
                      'exposure %5.1f%%'
                      % (p['sharpe'], p['total_return'] * 100,
                         p['round_trips'], p['exposure'] * 100))
        rows.append(rec)
        print('')

    if not rows:
        print('nothing ran')
        return

    df = pd.DataFrame(rows)
    path = os.path.join(out_dir, 'proto_halflife_window.csv')
    df.to_csv(path, index=False)
    print('written: %s' % path)
    return df


if __name__ == '__main__':
    main()
