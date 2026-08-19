"""The pairs strategy itself, parameterised so one definition serves the
research grid, the out-of-sample test, and live trading.

Signal
------
Rolling OLS of log(A) on log(B) over `lookback` sessions gives a hedge ratio;
the residual of that fit, divided by its own standard deviation, is the z-score.
Working in logs keeps the hedge ratio a scale-free elasticity -- see the note in
screen_pairs.py for why levels give a hedge that silently stops hedging.

Positions are dollar-neutral: the two legs get equal and opposite gross weight,
so beta drives the *signal* but not the sizing. Sizing by beta as well would
concentrate the book in whichever leg happened to be more volatile.

Risk controls
-------------
Every backtest gets set_max_leverage / set_max_position_size, plus a z-score
stop. Those are not live-only decorations: a backtest run without them reports
returns the real account could never have taken, which makes the whole research
loop lie to you.

The z-stop is the one that matters for pairs. A spread that keeps widening past
|z| = STOP_Z is evidence the relationship broke (merger, guidance cut, index
reconstitution), and the mean-reversion premise no longer holds. Without it the
strategy doubles down into exactly the divergences that end pairs books.
"""
from __future__ import print_function

import os
import sys

import numpy as np

from zipline.api import (order_target_percent, record, set_commission,
                         set_max_leverage, set_slippage, symbol)
from zipline.finance import commission, slippage
from zipline.finance.asset_restrictions import StaticRestrictions  # noqa: F401

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402


def zscore_and_beta(prices_a, prices_b):
    """(z, beta) from the log-price residual, or (None, None) if unusable.

    The beta bounds are enforced HERE, not only in the screen. screen_pairs
    checks MIN_BETA/MAX_BETA once, on the full in-sample window; this refits on
    a rolling `lookback` window, and the two do not have to agree. Measured on
    the 2026-08-14 run, AMZN/GOOG passed the screen at beta=1.23 and then spent
    110 of 252 out-of-sample sessions with a rolling beta outside [0.30, 3.00].

    A beta near zero means the fit found no relationship over the window, so
    the "spread" degenerates to log(A) minus a constant -- a single-name bet
    with the second leg attached as an arbitrary dollar hedge. That is the
    failure the screen's bounds exist to prevent, so they have to hold at the
    moment the signal is used, not just at admission.
    """
    a = np.asarray(prices_a, dtype=float)
    b = np.asarray(prices_b, dtype=float)
    if np.isnan(a).any() or np.isnan(b).any():
        return None, None
    if (a <= 0).any() or (b <= 0).any():
        return None, None
    la, lb = np.log(a), np.log(b)
    if lb.std() == 0:
        return None, None
    beta, intercept = np.polyfit(lb, la, 1)
    # abs(): a negative beta is a valid inverse relationship, and the bounds
    # describe the magnitude of the hedge, not its direction.
    if not (config.MIN_BETA <= abs(beta) <= config.MAX_BETA):
        return None, None
    spread = la - (beta * lb + intercept)
    sd = spread.std()
    if sd == 0:
        return None, None
    return float(spread[-1] / sd), float(beta)


def make_algo(sym_a, sym_b, lookback, entry_z, exit_z,
              max_gross=None, stop_z=None):
    """Build (initialize, handle_data, before_trading_start) for one pair.

    Returned as a closure rather than read from globals so the parameter grid
    can run many variants in one process without them stepping on each other.
    """
    max_gross = config.MAX_GROSS if max_gross is None else max_gross
    stop_z = config.STOP_Z if stop_z is None else stop_z

    def _setup(context):
        # initialize() is NOT called in live trading by zipline-trader, so every
        # piece of setup has to be safe to run repeatedly from
        # before_trading_start. This duplication is load-bearing; see CLAUDE.md.
        if getattr(context, '_ready', False):
            return
        context.a = symbol(sym_a)
        context.b = symbol(sym_b)
        context.lookback = lookback
        context.entry_z = entry_z
        context.exit_z = exit_z
        context.max_gross = max_gross
        context.stop_z = stop_z
        context.n_entries = 0
        context.n_stops = 0
        context._ready = True

    def initialize(context):
        _setup(context)
        if config.REALISTIC_COSTS:
            # Alpaca charges no commission; what is paid is the spread plus SEC
            # and FINRA fees on sells. The old PerShare($0.005, $1 min) was an
            # Interactive Brokers schedule and does not describe this account.
            import costs
            costs.apply()
        else:
            set_commission(commission.PerShare(
                cost=config.COMMISSION_PER_SHARE,
                min_trade_cost=config.COMMISSION_MIN))
            set_slippage(slippage.VolumeShareSlippage())
        set_max_leverage(config.MAX_LEVERAGE)

    def before_trading_start(context, data):
        _setup(context)

    def handle_data(context, data):
        hist = data.history([context.a, context.b], 'price',
                            context.lookback, '1d')
        if len(hist) < context.lookback:
            return

        z, beta = zscore_and_beta(hist[context.a].values, hist[context.b].values)
        if z is None:
            record(z=np.nan, hedge_beta=np.nan, gross=context.account.leverage)
            return

        pos = context.portfolio.positions
        held = pos.get(context.a)
        in_market = held is not None and held.amount != 0

        # Per-leg cap, enforced here rather than via set_max_position_size:
        # that API takes a share count or a fixed dollar amount, neither of
        # which tracks a portfolio that has grown or shrunk.
        w = min(max_gross / 2.0, config.MAX_POSITION_PCT)

        gross = context.account.leverage

        if in_market and abs(z) >= context.stop_z:
            target_a, target_b = 0.0, 0.0      # relationship broke -- get out
            context.n_stops += 1
        elif in_market and abs(z) <= context.exit_z:
            target_a, target_b = 0.0, 0.0      # reverted -- take it
        elif in_market and gross > config.GROSS_REBALANCE_AT:
            # The legs drifted apart. Resize to target in the direction we are
            # already positioned -- do NOT re-read z here, or a position would
            # silently flip side on a drift event.
            sign = 1.0 if held.amount > 0 else -1.0
            target_a, target_b = sign * w, -sign * w
        elif not in_market and z >= context.entry_z:
            target_a, target_b = -w, +w        # A rich vs B
            context.n_entries += 1
        elif not in_market and z <= -context.entry_z:
            target_a, target_b = +w, -w        # A cheap vs B
            context.n_entries += 1
        else:
            record(z=z, hedge_beta=beta, gross=context.account.leverage)
            return

        if data.can_trade(context.a) and data.can_trade(context.b):
            order_target_percent(context.a, target_a)
            order_target_percent(context.b, target_b)

        record(z=z, hedge_beta=beta, gross=context.account.leverage)

    return initialize, handle_data, before_trading_start
