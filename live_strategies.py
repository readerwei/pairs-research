"""Registry of strategies the live runner can execute.

A strategy contributes three things and nothing else:

  build(symbols, **params) -> (initialize, handle_data, before_trading_start)
  status(context)          -> one line describing what it is currently thinking
  metadata                 -> defaults, and whether it should be flat at the end

Everything operational -- credentials, the paper-endpoint refusal, pre-flight,
the watchdog, logging, heartbeats, price and fill reporting, reconciliation
against the broker, flattening -- belongs to live_runner.py and is identical for
every strategy. A new strategy should never have to re-implement any of it, and
should never be able to get it subtly wrong.

Adding one:

    class MyStrategy(LiveStrategy):
        name = 'my_strategy'
        description = 'what it does'
        defaults = {'lookback': 20}
        default_symbols = ['SPY']

        def build(self, symbols, lookback=20):
            return momentum_style_factory(symbols, lookback)

        def status(self, context):
            return 'lookback=%d' % context.lookback

    register(MyStrategy())
"""
from __future__ import print_function

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd  # noqa: E402

import momentum  # noqa: E402

# the live yaml universe, minus TCEHY which Alpaca serves no bars for
_UNIVERSE_33 = [
    'SPY', 'AAPL', 'MSFT', 'GOOG', 'AMZN', 'TSLA', 'AMD', 'NVDA', 'TSM', 'F',
    'BRK.B', 'BABA', 'AAL', 'ABNB', 'PLTR', 'HOOD', 'COUR', 'COST', 'V', 'MA',
    'PYPL', 'UNH', 'GEV', 'BAC', 'JPM', 'C', 'WFC', 'XPEV', 'NIO', 'ARKK',
    'NU', 'TLT', 'LI',
]


class LiveStrategy(object):
    """Interface. Subclass, set the class attributes, implement build()."""

    name = None
    description = ''
    defaults = {}                 # parameter name -> default value
    default_symbols = []
    flatten_on_exit = False       # True for test harnesses, not for strategies
    holds_overnight = True

    def build(self, symbols, **params):
        raise NotImplementedError

    def status(self, context):
        """One heartbeat line. Keep it short; it prints every N bars."""
        return ''

    def sizing(self, symbols, **params):
        """Human-readable sizing, shown in pre-flight before anything is armed."""
        return ''


# --------------------------------------------------------------------------
# Learn-Algorithmic-Trading Chapter 4, via momentum.py
# --------------------------------------------------------------------------
class _MomentumBase(LiveStrategy):
    defaults = {'n': 8}
    default_symbols = ['AMD', 'GOOG', 'UNH']
    short = False

    def build(self, symbols, n=8):
        return momentum.make_naive_momentum_algo(symbols, n, short=self.short)

    def status(self, context):
        cons = ' '.join('%s%+d' % (a.symbol, context.cons.get(a, 0))
                        for a in context.syms)
        held = [a.symbol for a in context.syms if context.target.get(a)]
        return '%s  (fires at %s%d)  long=%s' % (
            cons, chr(177), context.nb_conseq, ','.join(held) or 'none')

    def sizing(self, symbols, n=8):
        w = momentum.MAX_GROSS / float(len(symbols))
        return ('%.1f%% of equity per name when in, %.1f%% gross if all %d fire'
                % (w * 100, momentum.MAX_GROSS * 100, len(symbols)))


class NaiveMomentum(_MomentumBase):
    name = 'naive_momentum'
    description = ('long on N consecutive up minute bars, flat on N '
                   'consecutive down bars; never short')


class NaiveMomentumLongShort(_MomentumBase):
    name = 'naive_momentum_ls'
    description = ('as naive_momentum, but N down bars sells short instead of '
                   'merely exiting')
    short = True


class DoubleMA(LiveStrategy):
    name = 'double_ma'
    description = 'long while SMA(short) > SMA(long) on minute bars'
    defaults = {'short_window': 50, 'long_window': 200}
    default_symbols = ['AMD', 'GOOG', 'UNH']

    def build(self, symbols, short_window=50, long_window=200):
        return momentum.make_double_ma_algo(symbols, short_window, long_window)

    def status(self, context):
        held = [a.symbol for a in context.syms if context.target.get(a)]
        return ('SMA %d/%d  long=%s'
                % (context.short_window, context.long_window,
                   ','.join(held) or 'none'))

    def sizing(self, symbols, short_window=50, long_window=200):
        w = momentum.MAX_GROSS / float(len(symbols))
        return ('%.1f%% of equity per name when in, %.1f%% gross if all %d fire'
                % (w * 100, momentum.MAX_GROSS * 100, len(symbols)))


# --------------------------------------------------------------------------
# Plumbing test. Not a strategy; it has no edge and is not meant to.
# --------------------------------------------------------------------------
class PingPong(LiveStrategy):
    name = 'pingpong'
    description = ('test harness: flips every symbol between `shares` and flat '
                   'every `period` bars, so orders arrive on a schedule instead '
                   'of on a signal')
    defaults = {'period': 2, 'shares': 1}
    default_symbols = ['F', 'AAL', 'NIO']
    flatten_on_exit = True        # a test that leaves a position is not a test
    holds_overnight = False

    def build(self, symbols, period=2, shares=1):
        from zipline.api import order_target, record, symbol
        state = {'bar': 0}

        def _setup(context):
            for name, default in (('n_orders', 0), ('n_blocked', 0)):
                if getattr(context, name, None) is None:
                    setattr(context, name, default)
            if getattr(context, '_ready', False):
                return
            context.syms = []
            for s in symbols:
                try:
                    context.syms.append(symbol(s))
                except Exception:
                    pass
            context.holding = False
            context.period = period
            context.shares = shares
            context.target = {a: 0.0 for a in context.syms}
            context._ready = True

        def initialize(context):
            _setup(context)

        def before_trading_start(context, data):
            _setup(context)

        def handle_data(context, data):
            state['bar'] += 1
            if state['bar'] % context.period:
                return
            context.holding = not context.holding
            tgt = context.shares if context.holding else 0
            for asset in context.syms:
                if not data.can_trade(asset):
                    continue
                order_target(asset, tgt)
                context.target[asset] = float(tgt)
                context.n_orders += 1
            record(gross=context.account.leverage)

        return initialize, handle_data, before_trading_start

    def status(self, context):
        return ('%s %d share(s) of %s'
                % ('holding' if context.holding else 'flat', context.shares,
                   ','.join(a.symbol for a in context.syms)))

    def sizing(self, symbols, period=2, shares=1):
        return ('%d share(s) per name, flipping every %d bars -- a round trip '
                'every %d minutes' % (shares, period, 2 * period))


class SlippageProbe(LiveStrategy):
    """Measure what a real market order actually costs, symbol by symbol.

    Backtests model execution; this measures it. One symbol at a time,
    round-robin: buy `notional` dollars at market, hold `hold_bars` minutes,
    sell at market. For every leg it records what the algorithm saw, the live
    bid/ask at submission, and the price it actually got.

    One position at a time on purpose. Firing 33 orders at once would put ~$33k
    of directional exposure on the book to measure a few cents of spread, and
    the fills would compete for the same buying power. Sequential keeps
    exposure at one `notional` and makes each measurement independent.

    Sized in dollars, not shares, so the numbers are comparable: one share of
    NIO and one of AMD are not the same experiment. Round lots would be cleaner
    still -- odd lots can route differently -- but 100 shares of AMD is $48,800,
    which is not a reasonable thing to risk to measure a spread.

    Writes runs/slippage_probe_<date>.csv, one row per leg.
    """

    name = 'slippage_probe'
    description = ('measurement harness: buys and sells each symbol in turn at '
                   'market and records quoted vs filled price')
    defaults = {'notional': 1000, 'hold_bars': 2}
    default_symbols = list(_UNIVERSE_33)
    flatten_on_exit = True
    holds_overnight = False

    def build(self, symbols, notional=1000, hold_bars=2):
        import csv
        import os as _os
        from zipline.api import order, record, symbol

        import alpaca_trade_api as tradeapi
        api = tradeapi.REST()

        out_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                'runs')
        if not _os.path.isdir(out_dir):
            _os.makedirs(out_dir)
        path = _os.path.join(out_dir, 'slippage_probe_%s.csv'
                             % pd.Timestamp.now(tz='America/New_York').date())
        new = not _os.path.exists(path)
        fh = open(path, 'a')
        writer = csv.writer(fh)
        if new:
            writer.writerow(['submitted_ny', 'symbol', 'side', 'shares',
                             'algo_price', 'bid', 'ask', 'mid', 'spread_c',
                             'fill_price', 'fill_ny', 'slip_vs_mid_c',
                             'slip_vs_touch_c', 'slip_bps', 'latency_s'])
            fh.flush()

        st = {'bar': 0, 'i': 0, 'open': None, 'pending': []}

        def _setup(context):
            for n, d in (('n_orders', 0), ('n_blocked', 0)):
                if getattr(context, n, None) is None:
                    setattr(context, n, d)
            if getattr(context, '_ready', False):
                return
            context.syms = []
            for s in symbols:
                try:
                    context.syms.append(symbol(s))
                except Exception:
                    pass
            context.notional = notional
            context.hold_bars = hold_bars
            context.target = {a: 0.0 for a in context.syms}
            context.probe_done = 0
            context._ready = True

        def initialize(context):
            _setup(context)

        def before_trading_start(context, data):
            _setup(context)

        def _quote(sym):
            try:
                q = api.get_latest_quote(sym)
                return float(q.bp), float(q.ap)
            except Exception:
                return float('nan'), float('nan')

        def _drain(context):
            """Match submitted orders to fills and write a row each."""
            if not st['pending']:
                return
            try:
                closed = {o.id: o for o in api.list_orders(status='closed',
                                                           limit=50)}
            except Exception:
                return
            still = []
            for rec in st['pending']:
                o = closed.get(rec['id'])
                if o is None or not o.filled_avg_price:
                    still.append(rec)
                    continue
                fill = float(o.filled_avg_price)
                mid = rec['mid']
                touch = rec['ask'] if rec['side'] == 'buy' else rec['bid']
                sign = 1.0 if rec['side'] == 'buy' else -1.0
                filled_at = pd.Timestamp(o.filled_at).tz_convert(
                    'America/New_York')
                writer.writerow([
                    rec['submitted'].strftime('%H:%M:%S'), rec['symbol'],
                    rec['side'], rec['shares'],
                    round(rec['algo_price'], 4), round(rec['bid'], 4),
                    round(rec['ask'], 4), round(mid, 4),
                    round(100 * (rec['ask'] - rec['bid']), 2),
                    round(fill, 4), filled_at.strftime('%H:%M:%S'),
                    round(100 * sign * (fill - mid), 2),
                    round(100 * sign * (fill - touch), 2),
                    round(1e4 * sign * (fill - mid) / mid, 3) if mid else '',
                    round((filled_at - rec['submitted']).total_seconds(), 2),
                ])
                context.probe_done += 1
            fh.flush()
            st['pending'] = still

        def _submit(context, data, asset, side, shares):
            bid, ask = _quote(asset.symbol)
            try:
                algo_price = float(data.current(asset, 'price'))
            except Exception:
                algo_price = float('nan')
            before = {o.id for o in api.list_orders(status='all', limit=20)}
            order(asset, shares if side == 'buy' else -shares)
            context.n_orders += 1
            # the id zipline generated is not exposed, so identify the new order
            # by difference rather than guessing
            try:
                after = [o for o in api.list_orders(status='all', limit=20)
                         if o.id not in before and o.symbol == asset.symbol]
            except Exception:
                after = []
            if after:
                st['pending'].append({
                    'id': after[0].id, 'symbol': asset.symbol, 'side': side,
                    'shares': shares, 'algo_price': algo_price,
                    'bid': bid, 'ask': ask, 'mid': (bid + ask) / 2.0,
                    'submitted': pd.Timestamp.now(tz='America/New_York'),
                })

        def handle_data(context, data):
            st['bar'] += 1
            _drain(context)

            if st['open'] is None:
                if st['bar'] % context.hold_bars:
                    return
                asset = context.syms[st['i'] % len(context.syms)]
                st['i'] += 1
                if not data.can_trade(asset):
                    return
                try:
                    px = float(data.current(asset, 'price'))
                except Exception:
                    return
                if not px or px != px:
                    return
                shares = max(1, int(context.notional / px))
                _submit(context, data, asset, 'buy', shares)
                st['open'] = {'asset': asset, 'shares': shares,
                              'bar': st['bar']}
                return

            if st['bar'] - st['open']['bar'] >= context.hold_bars:
                o = st['open']
                if data.can_trade(o['asset']):
                    _submit(context, data, o['asset'], 'sell', o['shares'])
                    st['open'] = None
            record(gross=context.account.leverage)

        return initialize, handle_data, before_trading_start

    def status(self, context):
        return ('probed %d legs, %d/%d symbols visited'
                % (getattr(context, 'probe_done', 0),
                   min(len(context.syms), getattr(context, 'probe_i', 0)) or 0,
                   len(context.syms)))

    def sizing(self, symbols, notional=1000, hold_bars=2):
        return ('$%d notional, one symbol at a time, %d-minute holds -- max '
                'exposure $%d across %d symbols'
                % (notional, hold_bars, notional, len(symbols)))


_REGISTRY = {}


def register(strategy):
    _REGISTRY[strategy.name] = strategy


def get(name):
    if name not in _REGISTRY:
        raise SystemExit('unknown strategy %r; known: %s'
                         % (name, ', '.join(sorted(_REGISTRY))))
    return _REGISTRY[name]


def names():
    return sorted(_REGISTRY)


for _s in (NaiveMomentum(), NaiveMomentumLongShort(), DoubleMA(), PingPong(),
           SlippageProbe()):
    register(_s)
