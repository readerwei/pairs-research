"""Bundle -> DataFrame helpers shared by the screen and the reports.

Reading prices outside a zipline algorithm needs a DataPortal wired to the
bundle by hand; this keeps that boilerplate in one place.
"""
from __future__ import print_function

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402


_CACHE = {}


def load_bundle():
    if 'bundle' not in _CACHE:
        from zipline.data import bundles
        from ingest_research import register_readonly
        register_readonly()
        _CACHE['bundle'] = bundles.load(config.BUNDLE)
    return _CACHE['bundle']


def available_symbols():
    data = load_bundle()
    return sorted(a.symbol for a in data.asset_finder.retrieve_all(
        data.asset_finder.sids))


def close_prices(start, end, symbols=None):
    """Daily closes as a DataFrame indexed by session, columns = symbols.

    Bars are split/dividend adjusted at ingest (alpaca_api.py passes
    adjustment='all'), so no further adjustment is applied here.
    """
    from zipline.data.data_portal import DataPortal

    data = load_bundle()
    cal = config.nyse()
    if symbols is None:
        symbols = available_symbols()

    dp = DataPortal(
        data.asset_finder, trading_calendar=cal,
        first_trading_day=data.equity_daily_bar_reader.first_trading_day,
        equity_daily_reader=data.equity_daily_bar_reader,
        adjustment_reader=data.adjustment_reader)

    # The bundle's first session can be one or two days later than the range we
    # asked the ingest for (the first calendar day may predate the first bar).
    # Requesting a window that starts before it makes get_history_window raise.
    first = data.equity_daily_bar_reader.first_trading_day
    if first is not None and start < first:
        start = first

    sessions = cal.sessions_in_range(start, end)
    assets = []
    for s in symbols:
        try:
            assets.append(data.asset_finder.lookup_symbol(s, sessions[-1]))
        except Exception:
            pass

    px = dp.get_history_window(assets, sessions[-1], len(sessions),
                               '1d', 'close', 'daily')
    px.columns = [a.symbol for a in px.columns]
    return px


def candidate_pairs():
    """Every within-group symbol pair that survived ingest, as (a, b) tuples."""
    have = set(available_symbols())
    out = []
    if config.SCREEN_WITHIN_GROUPS_ONLY:
        for group, syms in sorted(config.UNIVERSE_GROUPS.items()):
            members = sorted(s for s in syms if s in have)
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    out.append((members[i], members[j], group))
    else:
        members = sorted(have)
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                out.append((members[i], members[j], 'all'))
    return out
