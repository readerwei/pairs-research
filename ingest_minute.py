"""Ingest minute bars for symbols the production bundle does not carry.

    python ingest_minute.py --symbols QQQ --months 12
    python ingest_minute.py --list

The nightly `alpaca_api` bundle only holds the yaml's custom_asset_list, so
anything outside it -- QQQ, say -- has nowhere to come from. This writes a
separate bundle rather than widening the production one, so the live pipeline's
input stays exactly what cron produced.

Ingests BOTH intervals. A minute-only bundle leaves the daily reader's
first_trading_day as NaT, and DataPortal reads that field while constructing
whatever data_frequency says, so every run_algorithm against it dies with
KeyError: 'NaT' before the first bar.

Alpaca serves 1-minute bars back to 2016-01-01.

Consumers select it with the MINUTE_BUNDLE environment variable:

    MINUTE_BUNDLE=research_1m python backtest_momentum.py --one double_ma ...
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

DEFAULT_BUNDLE = 'research_1m'


def session_range(months, cal=None):
    cal = cal or config.nyse()
    end = config.last_completed_session(cal)
    start = end - timedelta(days=int(months * 30.5))
    while not cal.is_session(start):
        start -= timedelta(days=1)
    return start, end


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', nargs='+', default=['QQQ'])
    ap.add_argument('--months', type=int, default=12)
    ap.add_argument('--bundle', default=DEFAULT_BUNDLE)
    ap.add_argument('--list', action='store_true')
    args = ap.parse_args()

    os.environ['MINUTE_BUNDLE'] = args.bundle
    import minute_bundle
    minute_bundle.BUNDLE = args.bundle

    if args.list:
        minute_bundle.describe()
        return

    config.load_alpaca_env()
    start, end = session_range(args.months)
    syms = [s.upper() for s in args.symbols]
    print('bundle   : %s' % args.bundle)
    print('symbols  : %s' % ', '.join(syms))
    print('sessions : %s -> %s (%d months)' % (start.date(), end.date(),
                                               args.months))

    from zipline.data.bundles import alpaca_api as alpaca_bundle
    from zipline.data.bundles import register
    from zipline.data import bundles as bundles_module

    alpaca_bundle.ASSETS = syms
    register(args.bundle, alpaca_bundle.api_to_bundle(interval=['1d', '1m']),
             calendar_name='NYSE', start_session=start, end_session=end,
             minutes_per_day=390)
    alpaca_bundle.initialize_client()

    t0 = time.time()
    bundles_module.ingest(args.bundle, os.environ, assets_versions=(),
                          show_progress=True)
    print('--- took %s ---' % timedelta(seconds=time.time() - t0))
    minute_bundle.describe()


if __name__ == '__main__':
    main()
