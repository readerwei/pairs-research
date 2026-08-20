"""Build the HDF5 files ML4T chapter 9 notebooks 05-07 expect, from our bundle.

    python build_nb_data.py                 # writes data.h5 + backtest.h5 here
    python build_nb_data.py --outdir ~/Documents/machine-learning-for-trading/09_time_series_models

The notebooks are written against Jansen's `assets.h5` (stooq US stocks/ETFs).
Rather than patch every cell, this reproduces the same keys and index shapes
from the `pairs_research` bundle so the notebook code runs unmodified.

What the notebooks read
-----------------------
  data.h5      'stocks/close'  wide DataFrame, DatetimeIndex x ticker
               'etfs/close'    same
               'tickers'       Series ticker -> name
  backtest.h5  'prices'        long OHLCV, MultiIndex (ticker, date)
               'tickers'       Series

Two adaptations are unavoidable, and both are recorded in `nb_meta`:

1. ETF/stock split. Jansen pairs 139 ETFs against 171 stocks (~23k pairs per
   period). Our 81 names split 14 ETF / 67 stock, so the same cross product is
   ~940 pairs -- fine, just smaller. The ETF list is hardcoded below because the
   bundle carries no asset-class field.

2. Ticker suffix. Jansen's stooq symbols end in '.US' and several notebook cells
   slice on that. We keep our bare symbols; nothing in 05-07 parses the suffix,
   it only ever round-trips as an index label.

Everything else -- the correlation prune, the ADF stationarity filter, the
Johansen/EG tests -- is left to the notebooks so their logic stays authoritative.

History note: the bundle starts 2022-07-06, so the notebooks' hardcoded
'2010':'2019' slices yield empty frames. Use the dates printed at the end, or
pass --print-dates for the exact substitutions.
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
import data as rdata  # noqa: E402

# The bundle has no asset-class metadata, so this is by hand. Everything not
# listed is treated as a stock.
ETFS = ['SPY', 'QQQ', 'IWM', 'DIA', 'XLE', 'XLF', 'XLK', 'XLV', 'XLP', 'XLI',
        'GLD', 'GDX', 'TLT', 'IEF', 'LQD', 'HYG', 'ARKK']


def ohlcv(start, end, symbols=None):
    """Long OHLCV frame, MultiIndex (ticker, date) -- backtest.h5 'prices' shape.

    data.close_prices only returns closes; backtrader needs all five fields, so
    this drives the DataPortal directly over each field.
    """
    from zipline.data.data_portal import DataPortal

    bundle = rdata.load_bundle()
    cal = config.nyse()
    if symbols is None:
        symbols = rdata.available_symbols()

    dp = DataPortal(
        bundle.asset_finder, trading_calendar=cal,
        first_trading_day=bundle.equity_daily_bar_reader.first_trading_day,
        equity_daily_reader=bundle.equity_daily_bar_reader,
        adjustment_reader=bundle.adjustment_reader)

    sessions = cal.sessions_in_range(start, end)
    assets = []
    for s in symbols:
        try:
            assets.append(bundle.asset_finder.lookup_symbol(s, sessions[-1]))
        except Exception:
            pass

    frames = {}
    for field in ('open', 'high', 'low', 'close', 'volume'):
        px = dp.get_history_window(assets, sessions[-1], len(sessions),
                                   '1d', field, 'daily')
        px.columns = [a.symbol for a in px.columns]
        frames[field] = px

    # wide-per-field -> long (ticker, date) x field
    out = (pd.concat(frames, axis=1)
             .stack(level=1)
             .rename_axis(['date', 'ticker'])
             .swaplevel()
             .sort_index())
    out = out[['open', 'high', 'low', 'close', 'volume']].dropna()
    out['volume'] = out['volume'].astype(np.int64)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--outdir', default=None,
                    help='where to write the .h5 files (default: this folder)')
    ap.add_argument('--print-dates', action='store_true')
    args = ap.parse_args()

    outdir = args.outdir or os.path.dirname(os.path.abspath(__file__))
    outdir = os.path.expanduser(outdir)
    if not os.path.isdir(outdir):
        os.makedirs(outdir)

    # rdata.session_range(), not config's -- the bundle stops at its last
    # ingest while the calendar knows about today, and the mismatch surfaces
    # as a bare KeyError deep inside the history loader.
    start, end = rdata.session_range()
    have = rdata.available_symbols()
    etfs_have = [s for s in ETFS if s in have]
    stocks_have = [s for s in have if s not in set(ETFS)]

    print('bundle   : %s' % config.BUNDLE)
    print('range    : %s -> %s' % (start.date(), end.date()))
    print('symbols  : %d  (%d etf / %d stock)'
          % (len(have), len(etfs_have), len(stocks_have)))
    print('pairs    : %d etf x stock combinations'
          % (len(etfs_have) * len(stocks_have)))

    closes = rdata.close_prices(start, end)
    # tz-naive: the notebooks slice with plain '2023' style strings and compare
    # against tz-naive Timestamps; a tz-aware index raises on those comparisons.
    closes.index = pd.DatetimeIndex(closes.index).tz_localize(None)

    stocks = closes[[s for s in stocks_have if s in closes.columns]].dropna(axis=1, how='all')
    etfs = closes[[s for s in etfs_have if s in closes.columns]].dropna(axis=1, how='all')
    tickers = pd.Series({s: s for s in closes.columns}, name='name')

    data_h5 = os.path.join(outdir, 'data.h5')
    stocks.to_hdf(data_h5, 'stocks/close')
    etfs.to_hdf(data_h5, 'etfs/close')
    tickers.to_hdf(data_h5, 'tickers')

    prices = ohlcv(start, end)
    prices.index = prices.index.set_levels(
        pd.DatetimeIndex(prices.index.levels[1]).tz_localize(None), level=1)

    backtest_h5 = os.path.join(outdir, 'backtest.h5')
    prices.to_hdf(backtest_h5, 'prices')
    tickers.to_hdf(backtest_h5, 'tickers')

    meta = pd.Series({
        'bundle': config.BUNDLE,
        'start': str(start.date()),
        'end': str(end.date()),
        'n_symbols': len(closes.columns),
        'n_etfs': etfs.shape[1],
        'n_stocks': stocks.shape[1],
        'source': 'alpaca via zipline pairs_research bundle',
        'adjusted': 'split/dividend adjusted at ingest',
    })
    meta.to_hdf(data_h5, 'nb_meta')
    meta.to_hdf(backtest_h5, 'nb_meta')

    print('\nwritten: %s' % data_h5)
    print('   stocks/close  %s' % (stocks.shape,))
    print('   etfs/close    %s' % (etfs.shape,))
    print('written: %s' % backtest_h5)
    print('   prices        %s  (%d tickers)'
          % (prices.shape, prices.index.get_level_values('ticker').nunique()))

    yrs = sorted(set(closes.index.year))
    print('\n--- notebook date substitutions -------------------------------')
    print('the notebooks hardcode 2010-2019; our data is %d-%d.'
          % (yrs[0], yrs[-1]))
    print("  nb05  select_assets(start=2010, end=2019)  ->  start=%d, end=%d"
          % (yrs[0], yrs[-1]))
    print("  nb06  stocks/etfs .loc['2015':]            ->  .loc['%d':]" % yrs[0])
    print("        dates = stocks.loc['2016-12':'2019-6']")
    print("                                             ->  .loc['%d-12':'%d-06']"
          % (yrs[0], yrs[-1]))
    print("  nb06  prices .loc[idx[tickers, '2016':'2019'], :]")
    print("                                             ->  '%d':'%d'"
          % (yrs[0], yrs[-1]))
    print('---------------------------------------------------------------')
    if args.print_dates:
        print('\nsessions per year:')
        print(closes.groupby(closes.index.year).size().to_string())


if __name__ == '__main__':
    main()
