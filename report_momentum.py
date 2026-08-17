"""Report on a momentum run folder: results table, pyfolio stats, tear sheets.

    python report_momentum.py                       # latest -momentum run
    python report_momentum.py --date 2026-08-14-momentum

Reads the perf pickles backtest_momentum.py saved, so nothing is re-simulated
and the out-of-sample window is still scored exactly once.
"""
from __future__ import print_function

import argparse
import glob
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
from report import tear_sheets  # noqa: E402


def latest_run():
    runs = sorted(glob.glob(os.path.join(config.RUNS_DIR, '*-momentum')))
    if not runs:
        raise SystemExit('no *-momentum run folder found')
    return runs[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    args = ap.parse_args()

    out_dir = (os.path.join(config.RUNS_DIR, args.date) if args.date
               else latest_run())
    name = os.path.basename(out_dir)

    lines = []
    add = lines.append
    add('# Minute-bar momentum research -- %s\n' % name)
    add('Strategies ported from Learn-Algorithmic-Trading Chapter 4:')
    add('`ch4_double_moving_average.py` and `ch4_naive_momentum_strategy2.py`.')
    add('Universe: the 33 tradeable names from the live yaml `custom_asset_list`.')
    add('Bars: 1-minute. Long-only, fixed slice per name, commission '
        '$0.005/share ($1 min) plus volume-share slippage.\n')

    for fn, title in [('grid_in_sample.csv', 'In-sample parameter grid'),
                      ('out_of_sample.csv', 'Out-of-sample (scored once)')]:
        path = os.path.join(out_dir, fn)
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path)
        cols = [c for c in ['strategy', 'params', 'total_return', 'sharpe',
                            'max_dd', 'transactions', 'tx_per_session',
                            'commission', 'cost_drag', 'avg_n_long',
                            'is_sharpe'] if c in df.columns]
        add('## %s\n' % title)
        add(df[cols].round(4).to_string(index=False))
        add('')

    stats = tear_sheets(out_dir)
    if stats:
        add('## Risk statistics (pyfolio, out-of-sample)\n')
        add(pd.DataFrame(stats).round(3).to_string())
        add('')
        add('Tear sheets: `tearsheets/*.png`')
        add('')

    path = os.path.join(out_dir, 'report.md')
    with open(path, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    print('\n'.join(lines))
    print('written: %s' % path)


if __name__ == '__main__':
    main()
