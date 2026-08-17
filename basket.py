"""Score a hand-picked basket of symbols under a strategy, with a tear sheet.

    python basket.py AMD GOOG UNH
    python basket.py AMD GOOG UNH --strategy naive_momentum
    python basket.py V MA --no-tearsheet

Reads the per-symbol return streams `symbol_study.py` already produced, so this
is instant -- no backtest re-run, and no extra look at any holdout.

Each name is equal-weighted and deployed at the strategy's gross target when its
own signal is long, so gross exposure floats with how many of them are in the
market. Returns are net of commission.

A word on what a number from this file means. If the basket was chosen by
looking at which names did well in this same data, the Sharpe it reports is a
description of the past, not an estimate of the future -- the selection already
used up the evidence. It is still worth computing: seeing how much of a basket's
result rests on one name is usually more informative than the headline.
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
import config  # noqa: E402


def load_returns(run_dir, strategy):
    path = os.path.join(run_dir, 'per_symbol_returns_%s.csv' % strategy)
    if not os.path.exists(path):
        raise SystemExit('no per-symbol returns at %s -- run symbol_study.py '
                         'for this strategy first' % path)
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df.index = pd.to_datetime([d.date() for d in df.index])
    return df


def stats(r, label, spy=None):
    r = r.dropna()
    sd = r.std()
    cum = (1 + r).cumprod()
    out = {
        'basket': label,
        'sessions': len(r),
        'total_return': cum.iloc[-1] - 1,
        'ann_return': cum.iloc[-1] ** (252.0 / len(r)) - 1,
        'sharpe': (r.mean() / sd * np.sqrt(252)) if sd else np.nan,
        'vol': sd * np.sqrt(252),
        'max_dd': (cum / cum.cummax() - 1).min(),
        'hit_rate': (r > 0).mean(),
    }
    if spy is not None:
        b = spy.reindex(r.index).fillna(0.0).values
        beta, alpha = np.polyfit(b, r.values, 1)
        resid = r.values - (beta * b + alpha)
        out.update({
            'beta': beta,
            'beta_explained': beta * ((1 + spy.reindex(r.index).fillna(0.0)).prod() - 1),
            'ann_alpha': alpha * 252,
            'info_ratio': (alpha / resid.std() * np.sqrt(252)) if resid.std() else np.nan,
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('symbols', nargs='+')
    ap.add_argument('--strategy', default='naive_momentum')
    ap.add_argument('--run', default=None, help='run folder name')
    ap.add_argument('--no-tearsheet', action='store_true')
    args = ap.parse_args()

    import backtest_momentum as bm
    cal = config.nyse()
    data, sess = bm.sessions(cal)
    run_dir = config.run_dir(args.run or (str(sess[-1].date()) + '-momentum'))

    rets = load_returns(run_dir, args.strategy)
    syms = [s.upper() for s in args.symbols]
    missing = [s for s in syms if s not in rets.columns]
    if missing:
        raise SystemExit('not in the study: %s' % ', '.join(missing))

    spy = bm.benchmark(data, cal, sess[2], sess[-1]).astype(float)
    spy.index = pd.to_datetime([d.date() for d in spy.index])

    basket = rets[syms].mean(axis=1)
    print('%s under %s' % ('/'.join(syms), args.strategy))
    print('period: %s -> %s\n' % (basket.index[0].date(), basket.index[-1].date()))

    rows = [stats(basket, '/'.join(syms), spy)]
    # each name alone, and the basket with each name removed -- the second is
    # what shows whether the basket is a portfolio or a single bet in disguise
    for s in syms:
        rows.append(stats(rets[s], s + ' alone', spy))
    if len(syms) > 1:
        for s in syms:
            rest = [x for x in syms if x != s]
            rows.append(stats(rets[rest].mean(axis=1), 'without ' + s, spy))
    rows.append(stats(rets.mean(axis=1), 'all %d' % rets.shape[1], spy))

    df = pd.DataFrame(rows).set_index('basket')
    cols = ['sessions', 'total_return', 'ann_return', 'sharpe', 'vol', 'max_dd',
            'hit_rate', 'beta', 'ann_alpha', 'info_ratio']
    print(df[cols].round(3).to_string())

    spy_tot = (1 + spy.reindex(basket.index).fillna(0.0)).prod() - 1
    print('\nSPY over the same period: %+.2f%%' % (spy_tot * 100))

    out = os.path.join(run_dir, 'basket_%s.csv' % '_'.join(syms))
    df.to_csv(out)
    print('written: %s' % out)

    if not args.no_tearsheet:
        import matplotlib
        matplotlib.use('Agg')
        import pyfolio_compat
        import pyfolio
        fig = pyfolio.create_returns_tear_sheet(basket, return_fig=True)
        pyfolio_compat.fix_date_axes(fig)
        ts = os.path.join(run_dir, 'tearsheets')
        if not os.path.isdir(ts):
            os.makedirs(ts)
        path = os.path.join(ts, 'basket_%s.png' % '_'.join(syms))
        fig.savefig(path, dpi=90, bbox_inches='tight')
        print('written: %s' % path)


if __name__ == '__main__':
    main()
