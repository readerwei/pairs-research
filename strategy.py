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


def zscore_and_beta(prices_a, prices_b, enforce_beta=True):
    """(z, beta) from the log-price residual, or (None, None) if unusable.

    `enforce_beta` applies the MIN_BETA/MAX_BETA bounds. It must be True when
    deciding whether to OPEN a position and False when managing one that is
    already open.

    The bounds exist because screen_pairs checks them once, on the full
    in-sample window, while this refits on a rolling `lookback` window and the
    two do not have to agree. On the 2026-08-14 run AMZN/GOOG passed the screen
    at beta=1.23 and then spent 110 of 252 out-of-sample sessions outside
    [0.30, 3.00]. A beta near zero degenerates the spread to log(A) minus a
    constant -- a single-name bet with the second leg attached as an arbitrary
    dollar hedge, which is what the bounds are there to prevent.

    But refusing to produce a z-score at all is only the right answer before
    entry. Applied to an open position it freezes the book: no z means no
    z-stop, no exit test, no rebalance, and the position is held unmanaged
    until beta happens to wander back into the band. Measured on the same
    window, AMZN/META would sit frozen for 86 sessions across 7 spells, the
    longest 31 sessions. Worse, the P&L stop added to cover a breaking fit
    could never fire in the beta->0 case its own comment cites, because the
    early return happened first.

    So: gate entries on the bounds, and keep managing what is already held.
    """
    a = np.asarray(prices_a, dtype=float)
    b = np.asarray(prices_b, dtype=float)
    if np.isnan(a).any() or np.isnan(b).any():
        return None, None
    if (a <= 0).any() or (b <= 0).any():
        return None, None
    la, lb = np.log(a), np.log(b)
    # Tolerance, not == 0: a constant price series gives a log-std of ~4e-16
    # rather than exactly zero, so the equality test never fired and polyfit
    # went on to return a meaningless slope from a degenerate fit. Anything
    # under 1e-12 in log space is a flat line to any resolution that matters.
    if lb.std() < 1e-12 or la.std() < 1e-12:
        return None, None
    beta, intercept = np.polyfit(lb, la, 1)
    # abs(): a negative beta is a valid inverse relationship, and the bounds
    # describe the magnitude of the hedge, not its direction.
    if enforce_beta and not (config.MIN_BETA <= abs(beta) <= config.MAX_BETA):
        return None, None
    spread = la - (beta * lb + intercept)
    sd = spread.std()
    if sd < 1e-12:
        return None, None
    return float(spread[-1] / sd), float(beta)


def spread_return(positions, asset_a, asset_b):
    """Unrealised P&L on the two legs, as a share of the capital they deployed.

    This is the pair's own return, NOT the portfolio's. Measuring the stop
    against portfolio_value instead was wrong twice over: it is diluted by
    gross (a dollar-neutral book at gross ~0.9 needs a ~17% adverse spread move
    to lose 15% of equity, so a -15% "spread stop" written that way is
    effectively unreachable), and portfolio_value also moves with anything else
    the account holds.

    Derived from broker positions rather than a value remembered at entry, so
    it survives a live restart. That matters here: initialize() is not called
    in live trading, so any state seeded at entry is lost on restart and a
    remembered entry value would come back None -- silently disabling the stop
    for exactly the position that was open across the restart.

    Returns None when the pair holds nothing, so the caller can tell "flat"
    apart from "flat P&L".
    """
    pnl = 0.0
    deployed = 0.0
    for asset in (asset_a, asset_b):
        pos = positions.get(asset)
        if pos is None or pos.amount == 0:
            continue
        # cost_basis is per share and signed by zipline for shorts; last_sale
        # is the current mark. amount * (mark - basis) gives signed P&L for
        # both long and short legs without special-casing the side.
        pnl += pos.amount * (pos.last_sale_price - pos.cost_basis)
        deployed += abs(pos.amount * pos.cost_basis)
    if deployed <= 0:
        return None
    return pnl / deployed


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
        context.n_pnl_stops = 0
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

        # Position state is read BEFORE the signal, because whether the beta
        # bounds may reject this bar depends on it: they gate entries, but an
        # open position must stay managed even when the fit degrades.
        pos = context.portfolio.positions
        held = pos.get(context.a)
        in_market = held is not None and held.amount != 0

        z, beta = zscore_and_beta(hist[context.a].values,
                                  hist[context.b].values,
                                  enforce_beta=not in_market)
        if z is None:
            # Flat: nothing to do. Holding: the window is genuinely unusable
            # (NaNs, non-positive prices, zero variance) rather than merely
            # out-of-band, so there is no signal to manage on. Fall back to the
            # one control that needs no model at all.
            if in_market and config.USE_PNL_STOP:
                pnl_pct = spread_return(pos, context.a, context.b)
                if pnl_pct is not None and pnl_pct <= config.STOP_LOSS_PCT:
                    if data.can_trade(context.a) and data.can_trade(context.b):
                        order_target_percent(context.a, 0.0)
                        order_target_percent(context.b, 0.0)
                    context.n_pnl_stops += 1
                record(z=np.nan, hedge_beta=np.nan, pair_pnl=pnl_pct,
                       gross=context.account.leverage)
                return
            record(z=np.nan, hedge_beta=np.nan, pair_pnl=np.nan,
                   gross=context.account.leverage)
            return

        # Per-leg cap, enforced here rather than via set_max_position_size:
        # that API takes a share count or a fixed dollar amount, neither of
        # which tracks a portfolio that has grown or shrunk.
        w = min(max_gross / 2.0, config.MAX_POSITION_PCT)

        # Leg weights. Dollar-neutral by default: equal and opposite, so beta
        # drives the signal but not the sizing. With USE_BETA_SIZING the second
        # leg is scaled by the hedge ratio so the position actually holds the
        # spread that was tested for stationarity -- see config.
        w_a, w_b = w, w
        if config.USE_BETA_SIZING:
            lo, hi = config.BETA_SIZING_CLAMP
            hedge = abs(beta)
            if lo <= hedge <= hi:
                # Keep total gross at 2w so the sizing choice does not silently
                # change leverage: split 2w between the legs in ratio 1:hedge.
                w_a = 2.0 * w / (1.0 + hedge)
                w_b = w_a * hedge
                cap = config.MAX_POSITION_PCT
                if max(w_a, w_b) > cap:      # re-scale rather than clip one leg
                    scale = cap / max(w_a, w_b)
                    w_a, w_b = w_a * scale, w_b * scale

        gross = context.account.leverage

        # P&L on the PAIR, from the positions themselves. Independent of the
        # z-score, so it still fires when the fit that produces z has broken --
        # which is the case the z-stop cannot cover, since a beta drifting to
        # zero shrinks the spread's sd and holds |z| inside the band while the
        # position bleeds.
        pnl_pct = spread_return(pos, context.a, context.b) if in_market else None

        if in_market and abs(z) >= context.stop_z:
            target_a, target_b = 0.0, 0.0      # relationship broke -- get out
            context.n_stops += 1
        elif (in_market and config.USE_PNL_STOP and pnl_pct is not None and
                pnl_pct <= config.STOP_LOSS_PCT):
            target_a, target_b = 0.0, 0.0      # bleeding regardless of z
            context.n_pnl_stops += 1
        elif in_market and abs(z) <= context.exit_z:
            target_a, target_b = 0.0, 0.0      # reverted -- take it
        elif in_market and gross > config.GROSS_REBALANCE_AT:
            # The legs drifted apart. Resize to target in the direction we are
            # already positioned -- do NOT re-read z here, or a position would
            # silently flip side on a drift event.
            sign = 1.0 if held.amount > 0 else -1.0
            target_a, target_b = sign * w_a, -sign * w_b
        elif not in_market and z >= context.entry_z:
            target_a, target_b = -w_a, +w_b    # A rich vs B
            context.n_entries += 1
        elif not in_market and z <= -context.entry_z:
            target_a, target_b = +w_a, -w_b    # A cheap vs B
            context.n_entries += 1
        else:
            record(z=z, hedge_beta=beta, gross=context.account.leverage,
                   pair_pnl=np.nan if pnl_pct is None else pnl_pct)
            return

        if data.can_trade(context.a) and data.can_trade(context.b):
            order_target_percent(context.a, target_a)
            order_target_percent(context.b, target_b)

        record(z=z, hedge_beta=beta, gross=context.account.leverage,
               pair_pnl=np.nan if pnl_pct is None else pnl_pct)

    return initialize, handle_data, before_trading_start
