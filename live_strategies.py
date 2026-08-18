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
import momentum  # noqa: E402


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


for _s in (NaiveMomentum(), NaiveMomentumLongShort(), DoubleMA(), PingPong()):
    register(_s)
