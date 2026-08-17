"""Minute-bar data source for the momentum research.

    python minute_bundle.py            # describe what's available
    python minute_bundle.py --check    # per-session bar coverage audit

Uses the production `alpaca_api` bundle. The nightly cron job already ingests
1-minute bars there for the whole yaml universe -- about a year of them
alongside the four years of daily bars -- so there is nothing to fetch and no
second copy to keep in sync.

Two other minute bundles on this box are dead ends, recorded here so nobody
re-derives it:

* `alpaca_api_1m` holds nine sessions (2026-08-04 -> 08-14).
* It was also ingested `interval=['1m']` only, which leaves its *daily* bcolz
  empty and `equity_daily_bar_reader.first_trading_day` as NaT. DataPortal reads
  that field while constructing, whatever `data_frequency` says, so every
  `run_algorithm` against it dies with `KeyError: 'NaT'` before the first bar.
  A minute-only bundle is not usable by zipline at all.
* `alpaca_api_3m` / `alpaca_api_both` cover three symbols over the same nine
  sessions. Fine for smoke tests, too small for research.

If you ever want minute history deeper than the nightly bundle keeps, Alpaca
serves 1-minute bars back to 2016-01-01; ingest a separate bundle with
`interval=['1d', '1m']` (both, per the NaT note above) rather than widening the
production one, so the live pipeline's input stays exactly what cron produced.
"""
from __future__ import print_function

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

BUNDLE = 'alpaca_api'

# The live trading universe as the yaml lists it, minus TCEHY: it is an OTC ADR
# and Alpaca serves no bars for it, so it never makes it into any bundle.
UNIVERSE = [
    'SPY', 'AAPL', 'MSFT', 'GOOG', 'AMZN', 'TSLA', 'AMD', 'NVDA', 'TSM', 'F',
    'BRK.B', 'BABA', 'AAL', 'ABNB', 'PLTR', 'HOOD', 'COUR', 'COST', 'V', 'MA',
    'PYPL', 'UNH', 'GEV', 'BAC', 'JPM', 'C', 'WFC', 'XPEV', 'NIO', 'ARKK',
    'NU', 'TLT', 'LI',
]


def register_readonly():
    """~/.zipline/extension.py is empty, so every consumer registers the name."""
    from zipline.data.bundles import core as bundles_module
    if BUNDLE in bundles_module.bundles:
        return

    def _noop(*a, **k):
        raise RuntimeError(
            '%s is produced by the nightly cron job '
            '(/home/wei/dailyexec/daily_ingest.sh); research does not ingest it'
            % BUNDLE)

    bundles_module.register(BUNDLE, _noop, calendar_name='NYSE',
                            minutes_per_day=390)


def load():
    from zipline.data import bundles
    register_readonly()
    return bundles.load(BUNDLE)


def minute_sessions(data=None, cal=None):
    """Sessions for which the bundle actually has minute bars."""
    data = data or load()
    cal = cal or config.nyse()
    mr = data.equity_minute_bar_reader
    dr = data.equity_daily_bar_reader
    # the minute reader's last_available_dt is a minute; clamp to the last
    # session the daily reader agrees exists
    end = min(pd.Timestamp(mr.last_available_dt).normalize().tz_convert('utc'),
              pd.Timestamp(dr.last_available_dt))
    return cal.sessions_in_range(mr.first_trading_day, end)


def describe():
    data = load()
    dr = data.equity_daily_bar_reader
    mr = data.equity_minute_bar_reader
    sess = minute_sessions(data)
    syms = sorted(a.symbol for a in
                  data.asset_finder.retrieve_all(data.asset_finder.sids))
    print('bundle          : %s' % BUNDLE)
    print('assets          : %d' % len(syms))
    print('daily bars      : %s -> %s' % (dr.first_trading_day.date(),
                                          dr.last_available_dt.date()))
    print('minute bars     : %s -> %s  (%d sessions)'
          % (sess[0].date(), sess[-1].date(), len(sess)))
    print('symbols         : %s' % ', '.join(syms))
    missing = sorted(set(UNIVERSE) - set(syms))
    if missing:
        print('NOT in bundle   : %s' % ', '.join(missing))
    return data


def check_coverage(n_samples=12):
    """What fraction of the 390 daily minutes actually have a bar?

    Worth running before trusting any minute backtest: a strategy reading a
    forward-filled or sparse series will produce confident nonsense.
    """
    data = load()
    cal = config.nyse()
    sess = minute_sessions(data, cal)
    syms = sorted(a.symbol for a in
                  data.asset_finder.retrieve_all(data.asset_finder.sids))
    assets = [data.asset_finder.lookup_symbol(s, sess[-1]) for s in syms]
    mr = data.equity_minute_bar_reader

    rows = []
    for i in np.linspace(0, len(sess) - 1, n_samples).astype(int):
        dt = sess[i]
        o, c = cal.open_and_close_for_session(dt)
        mins = cal.minutes_in_range(o, c)
        arr = mr.load_raw_arrays(['close'], mins[0], mins[-1],
                                 [a.sid for a in assets])[0]
        rows.append(pd.Series(np.isfinite(arr).mean(axis=0), index=syms,
                              name=str(dt.date())))
    cov = pd.DataFrame(rows)
    print('sessions sampled : %d of %d' % (n_samples, len(sess)))
    print('overall coverage : %.3f of 390 bars/session' % cov.values.mean())
    print('\nthinnest symbols:')
    print(cov.mean(axis=0).sort_values().head(8).round(3).to_string())
    return cov


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true',
                    help='audit per-session minute bar coverage')
    args = ap.parse_args()
    describe()
    if args.check:
        print()
        check_coverage()


if __name__ == '__main__':
    main()
