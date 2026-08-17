"""The two Chapter 4 momentum strategies from Learn-Algorithmic-Trading,
ported to zipline and run on 1-minute bars over the live yaml universe.

Source:
  https://github.com/PacktPublishing/Learn-Algorithmic-Trading/tree/master/Chapter4
    ch4_double_moving_average.py     -> make_double_ma_algo
    ch4_naive_momentum_strategy2.py  -> make_naive_momentum_algo

What the book wrote, and what had to change
-------------------------------------------
Both originals are signal generators over a single daily series (GOOG, 2001-2018)
that end in a matplotlib plot. There is no position sizing, no cost model, and no
portfolio. The signal logic below is kept exactly; only what the book left
undefined is supplied.

* **Double moving average** -- `signal = 1 where SMA(short) > SMA(long) else 0`,
  and `orders = signal.diff()`. Reproduced as written. The book's `orders` column
  is the reason this only trades on *transitions*: holding through a run of bars
  where the signal is unchanged issues no order. That detail matters much more on
  minute bars than on the daily bars it was written for.

* **Naive momentum** -- a running counter of consecutive up/down closes that
  fires when it hits exactly `nb_conseq_days`, and resets on a direction change.
  Reproduced with a per-symbol counter in `context` rather than a rolling window,
  because they are not the same rule: "the last N diffs were positive" fires on
  every bar of a long run, while the book fires once, on the bar the streak
  reaches N. A rolling-window version would trade several times more often and
  would not be the book's strategy.

Sizing: each symbol gets a fixed slice, `MAX_GROSS / len(universe)`, and is
independently long or flat -- the book's single-asset strategy run 33 times in
parallel. The alternative (equal weight across whatever is currently active)
resizes every open position whenever any one name flips, manufacturing turnover
that has nothing to do with the signal. On minute bars that distinction is worth
more than the strategy.

Long-only, matching the book: the sell signal exits to flat, it does not short.
"""
from __future__ import print_function

import os
import sys

import numpy as np

from zipline.api import (order_target_percent, record, set_commission,
                         set_max_leverage, set_slippage, symbol)
from zipline.finance import commission, slippage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

# Costs are the whole ballgame at minute frequency, so they are explicit here
# rather than inherited.
COMMISSION_PER_SHARE = 0.005
COMMISSION_MIN = 1.0
MAX_GROSS = 0.95
MAX_LEVERAGE = 1.15


def _common_setup(context, symbols):
    if getattr(context, '_ready', False):
        return
    context.syms = []
    for s in symbols:
        try:
            context.syms.append(symbol(s))
        except Exception:
            pass                      # not in the bundle; skip rather than die
    # Fixed slice per name. See the module docstring for why this is not
    # equal-weight-across-active.
    context.weight = MAX_GROSS / float(len(context.syms))
    context.target = {a: 0.0 for a in context.syms}
    context.n_orders = 0
    context._ready = True


def _apply_targets(context, data, new_target):
    """Order only where the target changed -- the book's `signal.diff()`."""
    for asset, tgt in new_target.items():
        if tgt == context.target[asset]:
            continue
        if not data.can_trade(asset):
            continue
        order_target_percent(asset, tgt * context.weight)
        context.target[asset] = tgt
        context.n_orders += 1


def _risk_controls():
    set_commission(commission.PerShare(cost=COMMISSION_PER_SHARE,
                                       min_trade_cost=COMMISSION_MIN))
    set_slippage(slippage.VolumeShareSlippage())
    set_max_leverage(MAX_LEVERAGE)


# --------------------------------------------------------------------------
# ch4_double_moving_average.py
# --------------------------------------------------------------------------
def make_double_ma_algo(symbols, short_window, long_window):
    def _setup(context):
        _common_setup(context, symbols)
        context.short_window = short_window
        context.long_window = long_window

    def initialize(context):
        _setup(context)
        _risk_controls()

    def before_trading_start(context, data):
        _setup(context)          # live never calls initialize(); see CLAUDE.md

    def handle_data(context, data):
        hist = data.history(context.syms, 'price', context.long_window, '1m')
        if len(hist) < context.long_window:
            return

        short_ma = hist.iloc[-context.short_window:].mean()
        long_ma = hist.mean()

        new_target = {}
        for asset in context.syms:
            s, l = short_ma[asset], long_ma[asset]
            if np.isnan(s) or np.isnan(l):
                new_target[asset] = context.target[asset]   # hold through gaps
            else:
                new_target[asset] = 1.0 if s > l else 0.0

        _apply_targets(context, data, new_target)
        record(n_long=sum(new_target.values()),
               gross=context.account.leverage)

    return initialize, handle_data, before_trading_start


# --------------------------------------------------------------------------
# ch4_naive_momentum_strategy2.py
# --------------------------------------------------------------------------
def make_naive_momentum_algo(symbols, nb_conseq):
    def _setup(context):
        _common_setup(context, symbols)
        context.nb_conseq = nb_conseq
        if not hasattr(context, 'cons'):
            context.cons = {a: 0 for a in context.syms}
            context.prior = {a: None for a in context.syms}

    def initialize(context):
        _setup(context)
        _risk_controls()

    def before_trading_start(context, data):
        _setup(context)

    def handle_data(context, data):
        prices = data.current(context.syms, 'price')

        new_target = dict(context.target)
        for asset in context.syms:
            price = prices[asset]
            if price is None or np.isnan(price):
                continue
            prior = context.prior[asset]
            if prior is None:
                context.prior[asset] = price
                continue

            # the book's counter, verbatim: reset on direction change
            if price > prior:
                if context.cons[asset] < 0:
                    context.cons[asset] = 0
                context.cons[asset] += 1
            elif price < prior:
                if context.cons[asset] > 0:
                    context.cons[asset] = 0
                context.cons[asset] -= 1
            context.prior[asset] = price

            # fires on ==, not >=: one signal per streak, as written
            if context.cons[asset] == context.nb_conseq:
                new_target[asset] = 1.0
            elif context.cons[asset] == -context.nb_conseq:
                new_target[asset] = 0.0

        _apply_targets(context, data, new_target)
        record(n_long=sum(new_target.values()),
               gross=context.account.leverage)

    return initialize, handle_data, before_trading_start


STRATEGIES = {
    'double_ma': make_double_ma_algo,
    'naive_momentum': make_naive_momentum_algo,
}
