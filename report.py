"""Turn a run folder into a verdict: what was tested, what survived, what (if
anything) is cleared for live trading.

    python report.py              # report on today's run folder
    python report.py --date 2026-08-14

Writes report.md next to the CSVs, and promoted.json listing the configs that
cleared the promotion gate. promoted.json is the only file downstream live
trading should read -- if it is empty, nothing earned a live allocation that
day, and that is a normal outcome rather than a failure.

Why the gate is strict
----------------------
The screen tests ~150 pairs and the grid tests ~15 configs each. Somewhere
around 2000 hypotheses get evaluated per run. At that search intensity the best
in-sample number is a near-certain overfit, so in-sample results are treated
only as a filter for what is worth testing out of sample, never as evidence.
Out-of-sample has to clear an absolute bar AND retain a share of the in-sample
Sharpe, on enough round trips for the number to mean anything.
"""
from __future__ import print_function

import argparse
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
import data as rdata  # noqa: E402

# promotion gate
MIN_OOS_SHARPE = 0.50
MIN_OOS_ROUND_TRIPS = 5
MAX_OOS_DRAWDOWN = -0.15
MIN_SHARPE_RETENTION = 0.50      # OOS sharpe as a fraction of IS sharpe


def tear_sheets(out_dir):
    """pyfolio stats + tear sheet PNGs for every OOS perf frame in the run.

    Reads the pickles backtest.py saved rather than re-running anything, so the
    out-of-sample window is still only simulated once.

    Returns {pair: stats Series}. Empty (with a printed note) if pyfolio is not
    installed -- the rest of the report does not depend on it.
    """
    perf_dir = os.path.join(out_dir, 'perf')
    if not os.path.isdir(perf_dir):
        return {}

    try:
        import matplotlib
        matplotlib.use('Agg')
        import pyfolio_compat  # noqa: F401  (patches pyfolio for pandas 1.x)
        import pyfolio
        from pyfolio.timeseries import perf_stats
        from pyfolio.utils import extract_rets_pos_txn_from_zipline
    except ImportError as e:
        print('pyfolio unavailable (%s) -- skipping tear sheets' % e)
        return {}

    ts_dir = os.path.join(out_dir, 'tearsheets')
    if not os.path.isdir(ts_dir):
        os.makedirs(ts_dir)

    stats = {}
    for fn in sorted(os.listdir(perf_dir)):
        if not fn.endswith('_oos.pkl'):
            continue
        pair = fn[:-len('_oos.pkl')].replace('_', '/')
        perf = pd.read_pickle(os.path.join(perf_dir, fn))
        rets, pos, txn = extract_rets_pos_txn_from_zipline(perf)
        stats[pair] = perf_stats(rets, positions=pos, transactions=txn)
        try:
            fig = pyfolio.create_returns_tear_sheet(
                rets, positions=pos, transactions=txn, return_fig=True)
            # Without this every time-series panel renders blank; see
            # pyfolio_compat.fix_date_axes.
            pyfolio_compat.fix_date_axes(fig)
            fig.savefig(os.path.join(ts_dir, '%s.png' % fn[:-len('_oos.pkl')]),
                        dpi=90, bbox_inches='tight')
        except Exception as e:
            print('  tear sheet for %s failed: %s' % (pair, e))
    return stats


def gate(row):
    """(passed, list of failure reasons)."""
    reasons = []
    if row['sharpe'] < MIN_OOS_SHARPE:
        reasons.append('OOS sharpe %.2f < %.2f' % (row['sharpe'], MIN_OOS_SHARPE))
    if row['round_trips'] < MIN_OOS_ROUND_TRIPS:
        reasons.append('only %d OOS round trips (need %d)'
                       % (row['round_trips'], MIN_OOS_ROUND_TRIPS))
    if row['max_dd'] < MAX_OOS_DRAWDOWN:
        reasons.append('OOS drawdown %.1f%% worse than %.0f%%'
                       % (row['max_dd'] * 100, MAX_OOS_DRAWDOWN * 100))
    # A config that lost money in sample must not reach live capital on the
    # strength of its out-of-sample number alone. Guarding the retention check
    # with `is_sharpe > 0` used to let exactly that through: CVX/XOM on the
    # 2026-08-14 data has IS sharpe -0.02 and OOS 1.25, and skipped retention
    # entirely. A negative in-sample Sharpe means the parameter search found
    # nothing, so a good OOS number is the sampling noise the holdout exists to
    # expose -- not evidence.
    if row['is_sharpe'] <= 0:
        reasons.append('IS sharpe %.2f is not positive -- OOS %.2f is '
                       'unsupported by the in-sample window'
                       % (row['is_sharpe'], row['sharpe']))
    elif row['sharpe'] < MIN_SHARPE_RETENTION * row['is_sharpe']:
        reasons.append('kept only %.0f%% of IS sharpe (need %.0f%%)'
                       % (100.0 * row['sharpe'] / row['is_sharpe'],
                          100 * MIN_SHARPE_RETENTION))
    return (not reasons), reasons


def residual_exposure(out_dir):
    """How much of each pair's P&L was NOT spread convergence.

    Dollar-neutral sizing holds the two legs 1:-1 regardless of the hedge
    ratio, so the position is only the tested spread when that ratio happens to
    be 1. Everything else leaks through as directional exposure to the second
    leg. This regresses each pair's daily returns on leg B's daily returns and
    reports the R^2 -- the share of P&L variance the pairs premise does not
    explain.

    Reported rather than gated on: the number is a property of the sizing
    choice, not a defect of the pair, and the point is to keep it visible while
    USE_BETA_SIZING is off. A pair whose hedge ratio sits near 1 will show a
    low value regardless.
    """
    perf_dir = os.path.join(out_dir, 'perf')
    if not os.path.isdir(perf_dir):
        return {}

    import numpy as np
    out = {}
    for fn in sorted(os.listdir(perf_dir)):
        if not fn.endswith('_oos.pkl'):
            continue
        stem = fn[:-len('_oos.pkl')]
        try:
            sym_a, sym_b = stem.split('_', 1)
        except ValueError:
            continue
        perf = pd.read_pickle(os.path.join(perf_dir, fn))
        rets = perf.returns.astype(float)
        try:
            px = rdata.close_prices(rets.index[0], rets.index[-1],
                                    symbols=[sym_a, sym_b])
        except Exception:
            continue
        if sym_b not in px.columns:
            continue
        rb = np.log(px[sym_b].astype(float)).diff()
        # Both indexes are tz-aware UTC but sit at different times of day: the
        # perf frame stamps each row at the session close (20:00Z) while the
        # bundle stamps bars at midnight. Joining them directly matches nothing
        # at all -- normalise to the session date on both sides.
        rb.index = pd.DatetimeIndex(rb.index).tz_convert(None).normalize()
        pair_idx = pd.DatetimeIndex(rets.index).tz_convert(None).normalize()
        joined = pd.DataFrame({'pair': rets.values}, index=pair_idx).join(
            rb.rename('legb'), how='inner').dropna()
        # Only sessions the pair actually held anything: flat days contribute a
        # zero return that would drag the correlation toward nothing and make
        # the leak look smaller than it is.
        if 'gross' in perf:
            g = perf['gross'].astype(float)
            g.index = pd.DatetimeIndex(g.index).tz_convert(None).normalize()
            joined = joined[g.reindex(joined.index).abs() > 0.01]
        if len(joined) < 30 or joined.legb.std() == 0:
            continue
        r = float(joined.pair.corr(joined.legb))
        out['%s/%s' % (sym_a, sym_b)] = {
            'corr_with_leg_b': r,
            'r_squared': r ** 2,
            'beta_on_leg_b': float(np.polyfit(joined.legb.values,
                                              joined.pair.values, 1)[0]),
            'sessions': len(joined),
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    args = ap.parse_args()

    out_dir = config.run_dir(args.date)
    date_str = os.path.basename(out_dir)
    # rdata.session_range(), not config's: config computes the window from the
    # exchange calendar, which knows about today, while the bundle stops at its
    # last ingest. When the two disagree -- pairs_research ending 2026-08-14
    # while the calendar has opened 2026-08-18 -- zipline raises a bare
    # KeyError from deep inside the history loader instead of saying the bundle
    # is stale.
    start, end = rdata.session_range()
    is_start, is_end, oos_start, oos_end = config.split_sessions(start, end)

    lines = []
    add = lines.append
    try:
        n_syms = len(rdata.available_symbols())
    except Exception:
        n_syms = len(config.UNIVERSE)

    add('# Pairs research -- %s\n' % date_str)
    add('- universe: %d symbols ingested (of %d requested), %d groups'
        % (n_syms, len(config.UNIVERSE), len(config.UNIVERSE_GROUPS)))
    add('- in-sample: %s -> %s' % (is_start.date(), is_end.date()))
    add('- out-of-sample: %s -> %s' % (oos_start.date(), oos_end.date()))
    add('')

    cand_path = os.path.join(out_dir, 'candidates.csv')
    oos_path = os.path.join(out_dir, 'out_of_sample.csv')

    if os.path.exists(cand_path):
        cands = pd.read_csv(cand_path)
        add('## Screen\n')
        add('%d pairs passed correlation + cointegration + half-life + hedge-ratio '
            'gates.\n' % len(cands))
        cols = ['sym_a', 'sym_b', 'group', 'corr', 'coint_p', 'beta',
                'half_life', 'last_z']
        add(cands[cols].head(config.TOP_N_PAIRS).to_string(index=False))
        add('')

    promoted = []
    if os.path.exists(oos_path):
        oos = pd.read_csv(oos_path)
        add('## Out-of-sample results\n')
        rows = []
        for _, r in oos.iterrows():
            ok, why = gate(r)
            rows.append({
                'pair': '%s/%s' % (r['sym_a'], r['sym_b']),
                'params': 'lb=%d ez=%.1f xz=%.1f' % (r['lookback'], r['entry_z'],
                                                     r['exit_z']),
                'is_sharpe': round(r['is_sharpe'], 2),
                'oos_sharpe': round(r['sharpe'], 2),
                'oos_return': '%+.2f%%' % (r['total_return'] * 100),
                'oos_dd': '%.2f%%' % (r['max_dd'] * 100),
                'round_trips': int(r['round_trips']),
                'verdict': 'PROMOTE' if ok else '; '.join(why),
            })
            if ok:
                promoted.append({
                    'sym_a': r['sym_a'], 'sym_b': r['sym_b'],
                    'lookback': int(r['lookback']),
                    'entry_z': float(r['entry_z']),
                    'exit_z': float(r['exit_z']),
                    'oos_sharpe': float(r['sharpe']),
                    'as_of': date_str,
                })
        add(pd.DataFrame(rows).to_string(index=False))
        add('')

    stats = tear_sheets(out_dir)

    resid = residual_exposure(out_dir)
    if resid:
        add('## Residual exposure to leg B (out-of-sample)\n')
        add('Share of each pair\'s P&L variance explained by leg B\'s own '
            'returns rather than by\nthe spread converging. Sizing is '
            '%s.\n'
            % ('hedge-ratio weighted (USE_BETA_SIZING=1)'
               if config.USE_BETA_SIZING else
               'dollar-neutral 1:-1, so any hedge ratio away from 1.0 leaks '
               'directional exposure'))
        rt = pd.DataFrame(resid).T
        rt.index.name = 'pair'
        add(rt.round(3).to_string())
        add('')
        add('A high r_squared does not make a pair invalid -- it means that '
            'much of what the\nbacktest scored was single-name direction, not '
            'the mean reversion being tested.')
        add('')

    if stats:
        add('## Risk statistics (pyfolio, out-of-sample)\n')
        table = pd.DataFrame(stats)
        add(table.round(3).to_string())
        add('')
        add('Tear sheets: `tearsheets/*.png`. Read Skew, Kurtosis and Tail ratio '
            'against the round-trip count above --\nthose moments describe a '
            'handful of events, not a distribution, when a pair traded a few '
            'times.')
        add('')

    add('## Verdict\n')
    if promoted:
        add('%d config(s) cleared the gate and are written to promoted.json:\n'
            % len(promoted))
        for p in promoted:
            add('- %s/%s lb=%d ez=%.1f xz=%.1f (OOS sharpe %.2f)'
                % (p['sym_a'], p['sym_b'], p['lookback'], p['entry_z'],
                   p['exit_z'], p['oos_sharpe']))
    else:
        add('Nothing cleared the promotion gate. No config is cleared for live '
            'capital today.')
    add('')

    report_path = os.path.join(out_dir, 'report.md')
    with open(report_path, 'w') as f:
        f.write('\n'.join(lines) + '\n')

    with open(os.path.join(out_dir, 'promoted.json'), 'w') as f:
        json.dump(promoted, f, indent=2)

    print('\n'.join(lines))
    print('written: %s' % report_path)
    print('written: %s' % os.path.join(out_dir, 'promoted.json'))


if __name__ == '__main__':
    main()
