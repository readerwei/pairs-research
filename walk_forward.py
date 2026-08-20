"""Quarterly walk-forward evaluation for the pairs research pipeline.

This runner is deliberately separate from ``backtest.py``.  It avoids a single
static 2022--2025 formation window by repeating the same research contract at
quarterly anchors:

    trailing two-year formation -> screen + IS grid -> following six-month test

The formation window is the only data used for screening and configuration
selection.  Each selected configuration is run exactly once in its forward
window.  Results are written below ``runs/walk_forward/<anchor>/``; this module
never submits broker orders.

The default session lengths (504 formation / 126 forward) are approximately two
years and six months of NYSE sessions.  They are parameters so a short fixture
or a different research horizon can be tested explicitly.

Usage::

    python walk_forward.py
    python walk_forward.py --formation-sessions 504 --forward-sessions 126
    python walk_forward.py --max-pairs 5 --date 2026-08-14
"""
from __future__ import print_function

import argparse
import math
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest  # noqa: E402
import config  # noqa: E402
import data as rdata  # noqa: E402
import screen_pairs  # noqa: E402


DEFAULT_FORMATION_SESSIONS = 504
DEFAULT_FORWARD_SESSIONS = 126
DEFAULT_MIN_OOS_ROUND_TRIPS = 5


def quarter_ends(sessions):
    """Return the last available session in each calendar quarter."""
    idx = pd.DatetimeIndex(sessions)
    if len(idx) == 0:
        return idx
    # pandas 1.1 supports PeriodIndex conversion for tz-aware timestamps.
    periods = idx.tz_convert(None).to_period('Q')
    grouped = pd.Series(idx, index=periods).groupby(level=0).max()
    return pd.DatetimeIndex(grouped.values)


def walk_forward_windows(sessions, formation_sessions, forward_sessions):
    """Yield ``(anchor, formation_start, formation_end, test_start, test_end)``.

    An anchor is the final available session of a calendar quarter.  The test
    starts on the next session, so no anchor-day close can leak into the forward
    result.  Windows without a complete trailing formation or forward period
    are omitted.
    """
    idx = pd.DatetimeIndex(sessions)
    positions = {ts: i for i, ts in enumerate(idx)}
    for anchor in quarter_ends(idx):
        anchor_i = positions.get(anchor)
        if anchor_i is None:
            continue
        formation_i = anchor_i - int(formation_sessions) + 1
        test_start_i = anchor_i + 1
        test_end_i = test_start_i + int(forward_sessions) - 1
        if formation_i < 0 or test_end_i >= len(idx):
            continue
        yield (anchor, idx[formation_i], idx[anchor_i],
               idx[test_start_i], idx[test_end_i])


def min_is_round_trips(formation_sessions, forward_sessions,
                       min_oos_round_trips=DEFAULT_MIN_OOS_ROUND_TRIPS):
    """Scale the forward activity floor back to the formation horizon."""
    if forward_sessions <= 0:
        raise ValueError('forward_sessions must be positive')
    return int(math.ceil(float(min_oos_round_trips) *
                         formation_sessions / forward_sessions)) + 1


def pick_winner(grid, min_round_trips):
    """Select the best formation Sharpe after the local activity floor."""
    if grid is None or grid.empty:
        return None
    eligible = grid[grid['round_trips'] >= int(min_round_trips)]
    if eligible.empty:
        return None
    # Stable tie-breaks make the selected configuration deterministic.
    cols = ['sharpe', 'round_trips', 'max_dd', 'lookback',
            'entry_z', 'exit_z']
    ordered = eligible.sort_values(cols,
                                   ascending=[False, False, False,
                                              True, True, True])
    return ordered.iloc[0]


def anchor_dir(anchor, output_root=None):
    root = output_root or os.path.join(config.RUNS_DIR, 'walk_forward')
    path = os.path.join(root, str(pd.Timestamp(anchor).date()))
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def run_window(anchor, formation_start, formation_end, test_start, test_end,
               max_pairs, min_is_rt, output_root=None):
    """Run one formation/test window and write immutable per-anchor artifacts."""
    out_dir = anchor_dir(anchor, output_root=output_root)
    px = rdata.close_prices(formation_start, formation_end)
    candidates = screen_pairs.screen(px).head(int(max_pairs)).copy()
    candidates.to_csv(os.path.join(out_dir, 'candidates.csv'), index=False)

    all_grid = []
    selected = []
    for _, candidate in candidates.iterrows():
        a, b = candidate['sym_a'], candidate['sym_b']
        grid = backtest.grid_for_pair(a, b, formation_start, formation_end,
                                      half_life=candidate.get('half_life'))
        if grid.empty:
            continue
        grid = grid.copy()
        grid['group'] = candidate['group']
        grid['anchor'] = str(pd.Timestamp(anchor).date())
        all_grid.append(grid)
        winner = pick_winner(grid, min_is_rt)
        if winner is not None:
            selected.append(winner)

    if all_grid:
        pd.concat(all_grid, ignore_index=True).to_csv(
            os.path.join(out_dir, 'grid_in_sample.csv'), index=False)

    oos_rows = []
    perf_dir = os.path.join(out_dir, 'perf')
    if selected and not os.path.isdir(perf_dir):
        os.makedirs(perf_dir)
    for winner in selected:
        try:
            metrics, perf = backtest.run_one(
                winner['sym_a'], winner['sym_b'], int(winner['lookback']),
                winner['entry_z'], winner['exit_z'], test_start, test_end)
        except Exception as exc:
            # A failed forward simulation is recorded, not silently promoted.
            oos_rows.append({
                'sym_a': winner['sym_a'], 'sym_b': winner['sym_b'],
                'lookback': winner['lookback'], 'entry_z': winner['entry_z'],
                'exit_z': winner['exit_z'], 'error': repr(exc),
            })
            continue
        metrics['anchor'] = str(pd.Timestamp(anchor).date())
        metrics['formation_start'] = str(pd.Timestamp(formation_start).date())
        metrics['formation_end'] = str(pd.Timestamp(formation_end).date())
        metrics['test_start'] = str(pd.Timestamp(test_start).date())
        metrics['test_end'] = str(pd.Timestamp(test_end).date())
        metrics['is_sharpe'] = winner['sharpe']
        metrics['is_round_trips'] = winner['round_trips']
        oos_rows.append(metrics)
        perf.to_pickle(os.path.join(
            perf_dir, '%s_%s_oos.pkl' % (winner['sym_a'], winner['sym_b'])))

    pd.DataFrame(oos_rows).to_csv(
        os.path.join(out_dir, 'out_of_sample.csv'), index=False)
    return candidates, pd.DataFrame(oos_rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--formation-sessions', type=int,
                        default=DEFAULT_FORMATION_SESSIONS)
    parser.add_argument('--forward-sessions', type=int,
                        default=DEFAULT_FORWARD_SESSIONS)
    parser.add_argument('--min-oos-round-trips', type=int,
                        default=DEFAULT_MIN_OOS_ROUND_TRIPS)
    parser.add_argument('--max-pairs', type=int, default=config.TOP_N_PAIRS)
    parser.add_argument('--output-root', default=None)
    args = parser.parse_args(argv)

    if args.formation_sessions < 30:
        parser.error('--formation-sessions must be at least 30')
    if args.forward_sessions < 1:
        parser.error('--forward-sessions must be positive')
    if args.max_pairs < 1:
        parser.error('--max-pairs must be positive')

    start, end = rdata.session_range()
    sessions = config.nyse().sessions_in_range(start, end)
    min_is_rt = min_is_round_trips(
        args.formation_sessions, args.forward_sessions,
        args.min_oos_round_trips)
    windows = list(walk_forward_windows(
        sessions, args.formation_sessions, args.forward_sessions))
    if not windows:
        raise RuntimeError('no complete walk-forward windows in bundle')

    print('formation sessions : %d' % args.formation_sessions)
    print('forward sessions   : %d' % args.forward_sessions)
    print('IS round-trip floor: %d' % min_is_rt)
    print('windows            : %d' % len(windows))

    summaries = []
    for window in windows:
        anchor, fs, fe, ts, te = window
        print('%s: formation %s -> %s; test %s -> %s' %
              (pd.Timestamp(anchor).date(), pd.Timestamp(fs).date(),
               pd.Timestamp(fe).date(), pd.Timestamp(ts).date(),
               pd.Timestamp(te).date()))
        _, oos = run_window(anchor, fs, fe, ts, te, args.max_pairs,
                            min_is_rt, output_root=args.output_root)
        if not oos.empty:
            summaries.append(oos)

    root = args.output_root or os.path.join(config.RUNS_DIR, 'walk_forward')
    if summaries:
        pd.concat(summaries, ignore_index=True).to_csv(
            os.path.join(root, 'summary.csv'), index=False)
    print('written: %s' % root)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
