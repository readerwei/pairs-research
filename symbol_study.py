"""Which symbols deserve capital -- tested as a procedure, not as a basket.

    python symbol_study.py                       # naive_momentum N=8
    python symbol_study.py --strategy double_ma --params 50 200
    python symbol_study.py --window 60 --hold 20

The question "which stocks are good for this strategy?" has an easy wrong answer:
rank all 33 over the past year and trade the winners. That is picking yesterday's
lottery numbers. AMD made +$1,779 in the last holdout and LI lost -$806, and
nothing in that fact says either repeats.

So what gets measured here is the *procedure* a live account would follow: every
`hold` sessions, rank symbols on the trailing `window` sessions and hold the top
K for the next block. Every session traded is out-of-sample with respect to the
ranking that selected it, and no single fixed holdout is consumed -- which
matters, because this project's 75-session holdout has already been looked at.

K = 1, 3, 5, 10 are compared against equal-weight-all-33 so concentration can be
judged against the alternative rather than in isolation.

How per-symbol returns are obtained
-----------------------------------
From ONE portfolio backtest, not 33 standalone ones. Each name gets a fixed
slice of gross and they never rebalance against each other, so a symbol's P&L
stream is already separable. (Timing says this is not just convenient: a
single-symbol minute backtest costs 0.61s/session against 0.80s for all 33 --
per-bar overhead dominates, not universe width -- so 33 standalone runs would
take 83 minutes to reproduce what one 4-minute run already contains.)

The residual coupling is that `order_target_percent` sizes off total portfolio
value, so a losing name very slightly shrinks everyone's dollar slice. That
biases nothing in the ranking, which is scale-free.
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
import backtest_momentum as bm  # noqa: E402
import momentum  # noqa: E402

COMMISSION_PER_SHARE = momentum.COMMISSION_PER_SHARE
COMMISSION_MIN = momentum.COMMISSION_MIN


def per_symbol_pnl(perf):
    """Daily P&L per symbol, net of commission, as a DataFrame.

    P&L for symbol s on day t is the change in its market value minus the cash
    put into it that day:

        pnl = (mv_t - mv_{t-1}) - sum(amount * price) - commission

    Commission is recomputed from the fills rather than read off the order
    objects: zipline's PerShare model is `max(min_trade_cost, cost * shares)`
    and reproducing it per transaction is exact, whereas perf.orders carries a
    running total that repeats on every day an order stays open.
    """
    mv = {}
    flows = {}
    for dt, positions in perf.positions.items():
        day_mv = {}
        for p in positions:
            day_mv[p['sid'].symbol] = p['amount'] * p['last_sale_price']
        mv[dt] = day_mv
    for dt, txns in perf.transactions.items():
        day = {}
        for t in txns:
            s = t['sid'].symbol
            comm = max(COMMISSION_MIN, COMMISSION_PER_SHARE * abs(t['amount']))
            day[s] = day.get(s, 0.0) + t['amount'] * t['price'] + comm
        flows[dt] = day

    mv = pd.DataFrame(mv).T.fillna(0.0).sort_index()
    flows = pd.DataFrame(flows).T.reindex(mv.index).fillna(0.0)
    flows = flows.reindex(columns=mv.columns, fill_value=0.0)
    return mv.diff().fillna(mv.iloc[0]) - flows


def to_returns(pnl, capital_base, n_names):
    """P&L -> return series per symbol, on the slice of capital each one ran."""
    return pnl / (float(capital_base) / n_names)


def summarize(rets):
    def stats(r):
        r = r.dropna()
        sd = r.std()
        return pd.Series({
            'total_return': (1 + r).prod() - 1,
            'sharpe': (r.mean() / sd * np.sqrt(252)) if sd else np.nan,
            'vol': sd * np.sqrt(252),
            'max_dd': ((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min(),
            'hit_rate': (r > 0).mean(),
            'n_active': (r != 0).sum(),
        })
    return rets.apply(stats).T.sort_values('sharpe', ascending=False)


def walk_forward(rets, window, hold, ks):
    """Rank on the trailing `window`, hold the top K for `hold` sessions, roll.

    Returns {K: return series}. Selection never sees the block it trades.
    """
    idx = rets.index
    out = {k: pd.Series(0.0, index=idx) for k in ks}
    picks = {k: [] for k in ks}
    start = window
    while start < len(idx):
        stop = min(start + hold, len(idx))
        train = rets.iloc[start - window:start]
        sd = train.std()
        score = (train.mean() / sd.replace(0.0, np.nan)) * np.sqrt(252)
        score = score.dropna().sort_values(ascending=False)
        block = rets.iloc[start:stop]
        for k in ks:
            sel = list(score.index[:k])
            if sel:
                out[k].iloc[start:stop] = block[sel].mean(axis=1).values
                picks[k].append((str(idx[start].date()), sel))
        start = stop
    for k in ks:
        out[k] = out[k].iloc[window:]
    return out, picks


def perf_line(r):
    r = r.dropna()
    sd = r.std()
    sharpe = (r.mean() / sd * np.sqrt(252)) if sd else np.nan
    cum = (1 + r).cumprod()
    dd = (cum / cum.cummax() - 1).min()
    return ('ret %+7.2f%%  sharpe %6.2f  vol %5.1f%%  maxdd %6.2f%%  '
            'hit %4.1f%%' % ((cum.iloc[-1] - 1) * 100, sharpe, sd * np.sqrt(252) * 100,
                             dd * 100, (r > 0).mean() * 100))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--strategy', default='naive_momentum')
    ap.add_argument('--params', nargs='+', type=int, default=[8])
    ap.add_argument('--window', type=int, default=60,
                    help='trailing sessions used to rank')
    ap.add_argument('--hold', type=int, default=20,
                    help='sessions a selection is held before re-ranking')
    args = ap.parse_args()

    cal = config.nyse()
    data, sess = bm.sessions(cal)
    start, end = sess[bm.WARMUP_SESSIONS], sess[-1]
    print('%s %s over %s -> %s (%d sessions)'
          % (args.strategy, args.params, start.date(), end.date(),
             len(sess) - bm.WARMUP_SESSIONS))

    m, perf = bm.run_one(args.strategy, tuple(args.params), start, end, data, cal)
    print('whole-universe run: %s' % bm.fmt(m))

    pnl = per_symbol_pnl(perf)
    check = pnl.sum().sum()
    print('per-symbol P&L reconstruction: $%.0f  (portfolio P&L $%.0f)'
          % (check, m['end_value'] - bm.CAPITAL_BASE))

    n = pnl.shape[1]
    rets = to_returns(pnl, bm.CAPITAL_BASE, n)

    out_dir = config.run_dir(str(sess[-1].date()) + '-momentum')
    rets.to_csv(os.path.join(out_dir, 'per_symbol_returns_%s.csv' % args.strategy))
    stats = summarize(rets)
    stats.to_csv(os.path.join(out_dir, 'per_symbol_%s.csv' % args.strategy))
    print('\n=== per symbol, whole period (descriptive -- NOT a selection) ===')
    print(stats.round(3).head(8).to_string())
    print('...')
    print(stats.round(3).tail(5).to_string())

    ks = [1, 3, 5, 10]
    wf, picks = walk_forward(rets, args.window, args.hold, ks)
    print('\n=== walk-forward: rank on trailing %d, hold %d, re-rank ==='
          % (args.window, args.hold))
    print('  (%d sessions traded, %d rebalances)'
          % (len(wf[ks[0]]), len(picks[ks[0]])))
    rows = {}
    for k in ks:
        print('  top %-2d  %s' % (k, perf_line(wf[k])))
        rows['top%d' % k] = wf[k]
    ew = rets.mean(axis=1).iloc[args.window:]
    print('  all %-2d  %s' % (n, perf_line(ew)))
    rows['all'] = ew

    pd.DataFrame(rows).to_csv(
        os.path.join(out_dir, 'walk_forward_%s.csv' % args.strategy))

    # Robustness: does the *procedure* work, or did it just find one stock?
    # Drop the single largest P&L contributor and re-run the whole walk-forward.
    # If the result collapses, what was measured is one lucky name, not a
    # repeatable way of choosing names -- and only the latter is worth capital.
    top_pnl = pnl.sum().sort_values(ascending=False)
    dominant = top_pnl.index[0]
    share = 100.0 * top_pnl.iloc[0] / top_pnl.sum() if top_pnl.sum() else np.nan
    print('\n=== robustness: same procedure without %s (%.0f%% of total P&L) ==='
          % (dominant, share))
    wf2, _ = walk_forward(rets.drop(columns=[dominant]), args.window,
                          args.hold, ks)
    for k in ks:
        print('  top %-2d  %s' % (k, perf_line(wf2[k])))
    ew2 = rets.drop(columns=[dominant]).mean(axis=1).iloc[args.window:]
    print('  all %-2d  %s' % (n - 1, perf_line(ew2)))
    pd.DataFrame(dict([('top%d' % k, wf2[k]) for k in ks] + [('all', ew2)])).to_csv(
        os.path.join(out_dir, 'walk_forward_ex_%s_%s.csv'
                     % (dominant.replace('.', ''), args.strategy)))

    print('\nselections over time (top 3):')
    for dt, sel in picks[3]:
        print('  %s  %s' % (dt, ', '.join(sel)))

    print('\nwritten: %s' % out_dir)


if __name__ == '__main__':
    main()
