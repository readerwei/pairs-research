"""Ingest the `pairs_research` bundle: daily bars for the candidate universe.

    python ingest_research.py            # ingest
    python ingest_research.py --list     # show what's already ingested, don't fetch

Deliberately separate from the nightly `alpaca_api` ingest. That bundle is the
live pipeline's input and only holds the yaml's custom_asset_list; this one is
wider and can be re-run at any time without touching production.

Reuses alpaca_api.py's fetch/write machinery (which already handles Alpaca's
paging, the out-of-session guard, and adjustment='all' for splits) and only
swaps the asset list.
"""
from __future__ import print_function

import argparse
import os
import sys
import time
from datetime import timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402


def register_research_bundle(start, end):
    """Register `pairs_research` against alpaca_api's ingest function."""
    from zipline.data.bundles import alpaca_api as alpaca_bundle
    from zipline.data.bundles import register

    # Pin the universe before anything calls list_assets(). alpaca_api caches
    # the result in a module global, so setting it here wins.
    alpaca_bundle.ASSETS = list(config.UNIVERSE)

    register(
        config.BUNDLE,
        alpaca_bundle.api_to_bundle(interval=['1d']),
        calendar_name='NYSE',
        start_session=start,
        end_session=end,
    )
    return alpaca_bundle


def register_readonly():
    """Register the bundle for *loading* only, without importing the Alpaca client.

    ~/.zipline/extension.py is empty on this box, so every consumer has to
    register the name itself before bundles.load() will find it.
    """
    from zipline.data.bundles import core as bundles_module

    if config.BUNDLE in bundles_module.bundles:
        return

    def _noop(*a, **k):
        raise RuntimeError('read-only handle; run ingest_research.py to ingest')

    start, end = config.session_range()
    bundles_module.register(
        config.BUNDLE, _noop, calendar_name='NYSE',
        start_session=start, end_session=end,
    )


def describe():
    """Print what the most recent ingest actually contains."""
    from zipline.data import bundles

    register_readonly()
    data = bundles.load(config.BUNDLE)
    finder = data.asset_finder
    sids = finder.sids
    syms = sorted(finder.retrieve_all(sids), key=lambda a: a.symbol)
    reader = data.equity_daily_bar_reader
    print('bundle       : %s' % config.BUNDLE)
    print('assets       : %d' % len(syms))
    print('sessions     : %s -> %s' % (reader.first_trading_day.date(),
                                       reader.last_available_dt.date()))
    print('symbols      : %s' % ', '.join(a.symbol for a in syms))
    missing = sorted(set(config.UNIVERSE) - {a.symbol for a in syms})
    if missing:
        print('NOT ingested : %s' % ', '.join(missing))
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--list', action='store_true',
                    help='describe the existing bundle instead of ingesting')
    args = ap.parse_args()

    if args.list:
        describe()
        return

    config.load_alpaca_env()
    start, end = config.session_range()
    print('universe : %d symbols' % len(config.UNIVERSE))
    print('sessions : %s -> %s' % (start.date(), end.date()))

    alpaca_bundle = register_research_bundle(start, end)
    alpaca_bundle.initialize_client()

    from zipline.data import bundles as bundles_module
    t0 = time.time()
    bundles_module.ingest(config.BUNDLE, os.environ,
                          assets_versions=(), show_progress=True)
    print('--- ingest took %s ---' % timedelta(seconds=time.time() - t0))

    describe()


if __name__ == '__main__':
    main()
