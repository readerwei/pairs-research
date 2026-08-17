"""Exercise the live trading path deliberately, with a strategy that has no edge.

    python live_smoketest.py --check
    python live_smoketest.py --max-seconds 300
    python live_smoketest.py --symbols AMD GOOG UNH --period 2 --shares 1

This is not a strategy. It is a test harness wearing one. Every `period` minute
bars it flips each symbol between `shares` and flat, so buys and sells arrive
predictably instead of waiting on a signal -- a round trip per symbol every
`2 * period` minutes, against roughly one per 3.3 *sessions* for the real
strategy.

Deterministic on purpose. When the point is to find out whether orders submit,
fill, and come back correctly, you want to know exactly what should have
happened and when. A noisy strategy makes a plumbing failure look like a signal
that did not fire.

Sizing is a fixed share count, not a percentage, and defaults to 1 share. The
notional cap refuses to start if the basket would exceed --max-notional, so a
typo in --shares cannot turn a plumbing test into a position.

What it actually checks
-----------------------
Each cycle it compares zipline's idea of the portfolio against a direct Alpaca
query and prints both. That comparison is the real test: zipline's portfolio in
live mode is a *projection* of the broker, and nothing in zipline-trader
reconciles the two. A partial fill, a manual trade, or a restart can put them
out of step silently, which is the failure mode worth knowing about before real
money is involved.

Paper only unless --allow-real-money is passed, same as live_momentum.py.
"""
from __future__ import print_function

import argparse
import os
import sys
import time
import warnings

import pandas as pd

warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
import minute_bundle  # noqa: E402
from live_momentum import preflight  # noqa: E402

from zipline.api import order_target, record, symbol  # noqa: E402


def make_pingpong_algo(symbols, period, shares, api, verify_every):
    """Flip every symbol between `shares` and flat every `period` bars."""
    state = {'bar': 0}

    def _setup(context):
        if getattr(context, '_ready', False):
            return
        context.syms = []
        for s in symbols:
            try:
                context.syms.append(symbol(s))
            except Exception:
                pass
        context.holding = False
        context.n_orders = 0
        context._ready = True

    def initialize(context):
        _setup(context)

    def before_trading_start(context, data):
        _setup(context)          # live never calls initialize()

    def _reconcile(context):
        """zipline's view vs the broker's, side by side."""
        zp = {a.symbol: int(p.amount)
              for a, p in context.portfolio.positions.items() if p.amount}
        try:
            br = {p.symbol: int(float(p.qty)) for p in api.list_positions()}
        except Exception as e:
            print('   broker query failed: %s' % e)
            return
        keys = sorted(set(zp) | set(br))
        if not keys:
            print('   positions: both flat')
            return
        for k in keys:
            z, b = zp.get(k, 0), br.get(k, 0)
            print('   %-6s zipline %+4d   broker %+4d   %s'
                  % (k, z, b, 'ok' if z == b else '*** MISMATCH ***'))

    def handle_data(context, data):
        state['bar'] += 1
        now = pd.Timestamp.now(tz='America/New_York').strftime('%H:%M:%S')

        if state['bar'] % period == 0:
            context.holding = not context.holding
            target = shares if context.holding else 0
            placed = []
            for asset in context.syms:
                if not data.can_trade(asset):
                    continue
                order_target(asset, target)
                context.n_orders += 1
                placed.append(asset.symbol)
            print('[%s] bar %-4d -> target %d share(s): %s  (orders so far %d)'
                  % (now, state['bar'], target, ', '.join(placed) or 'none',
                     context.n_orders))
            sys.stdout.flush()

        if verify_every and state['bar'] % verify_every == 0:
            print('[%s] bar %-4d reconcile' % (now, state['bar']))
            _reconcile(context)
            sys.stdout.flush()

        record(gross=context.account.leverage)

    return initialize, handle_data, before_trading_start


def _warn_on_conflict(symbols):
    """Refuse to trade a symbol another live algorithm on this box already owns.

    Two zipline live algorithms pointed at one symbol each believe they own the
    position, and each order_target from one looks like a fill against the
    other's intent. The result is not a race that resolves -- it is two
    strategies permanently undoing each other.
    """
    import glob
    import re
    conflicts = set()
    for path in glob.glob('/proc/[0-9]*/cmdline'):
        try:
            with open(path) as f:
                cmd = f.read().replace('\x00', ' ')
        except IOError:
            continue
        if 'live_momentum.py' not in cmd or 'live_smoketest' in cmd:
            continue
        others = re.findall(r'[A-Z][A-Z.]+', cmd.split('--symbols')[-1]) \
            if '--symbols' in cmd else ['AMD', 'GOOG', 'UNH']
        conflicts |= (set(symbols) & set(others))
    if conflicts:
        raise SystemExit(
            'refusing to start: live_momentum.py is already running and trading '
            '%s.\nPick different symbols for the smoke test, or stop that '
            'process first.' % ', '.join(sorted(conflicts)))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--symbols', nargs='+', default=['F', 'AAL', 'NIO'],
                    help='deliberately NOT the momentum basket -- two live\n                          algorithms trading the same name fight over the\n                          same position')
    ap.add_argument('--period', type=int, default=2,
                    help='bars between flips; a round trip takes 2x this')
    ap.add_argument('--shares', type=int, default=1)
    ap.add_argument('--max-notional', type=float, default=5000.0,
                    help='refuse to start if the basket would cost more')
    ap.add_argument('--verify-every', type=int, default=4,
                    help='bars between zipline-vs-broker reconciliations; 0 off')
    ap.add_argument('--max-seconds', type=float, default=600)
    ap.add_argument('--state-file', default='./live_smoketest.state')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--allow-real-money', action='store_true')
    args = ap.parse_args()

    syms = [s.upper() for s in args.symbols]
    print('=== live path smoke test: %d share(s) of %s, flipping every %d bars ==='
          % (args.shares, '/'.join(syms), args.period))
    _warn_on_conflict(syms)
    api, clock, resolved = preflight(syms, args.allow_real_money,
                                     show_sizing=False)

    quotes, notional = {}, 0.0
    for s in resolved:
        px = float(api.get_latest_trade(s).price)
        quotes[s] = px
        notional += px * args.shares
    print('notional     : %s = $%.2f'
          % (' + '.join('%s $%.2f' % (s, quotes[s] * args.shares)
                        for s in resolved), notional))
    if notional > args.max_notional:
        raise SystemExit('refusing to start: $%.2f exceeds --max-notional $%.2f'
                         % (notional, args.max_notional))

    rt = 2 * args.period
    print('expected     : a round trip per symbol every %d minutes, so about '
          '%.0f trades/hour across %d names'
          % (rt, 60.0 / rt * 2 * len(resolved), len(resolved)))

    if args.check:
        print('\n--check: pre-flight only, nothing armed.')
        return

    if not clock.is_open:
        print('\nMarket is CLOSED -- orders will not fill. Run this during the '
              'session or it tests nothing.')

    from logbook import StderrHandler, WARNING
    StderrHandler(level=WARNING).push_application()

    from zipline import run_algorithm
    from zipline.gens.brokers.alpaca_broker import ALPACABroker

    minute_bundle.register_readonly()
    broker = ALPACABroker()
    deadline = time.time() + args.max_seconds

    init, handle, bts = make_pingpong_algo(resolved, args.period, args.shares,
                                           api, args.verify_every)

    print('\nrunning for %.0fs\n' % args.max_seconds)
    perf = run_algorithm(
        start=pd.Timestamp.utcnow().normalize() - pd.Timedelta(days=5),
        end=pd.Timestamp.utcnow().normalize() + pd.Timedelta(days=1),
        initialize=init, handle_data=handle, before_trading_start=bts,
        capital_base=float(api.get_account().equity),
        bundle=minute_bundle.BUNDLE,
        trading_calendar=config.nyse(),
        data_frequency='minute',
        broker=broker,
        state_filename=args.state_file,
        stop_execution_callback=lambda e: time.time() > deadline,
    )

    print('\n=== after the run ===')
    filled = [o for o in api.list_orders(status='all', limit=100)
              if o.symbol in resolved]
    print('orders touching %s: %d' % ('/'.join(resolved), len(filled)))
    for o in filled[:12]:
        print('   %s %-5s %-4s qty %-4s %s'
              % (o.submitted_at.strftime('%H:%M:%S'), o.symbol, o.side,
                 o.qty, o.status))
    pos = api.list_positions()
    print('broker positions now: %s'
          % (', '.join('%s %s' % (p.symbol, p.qty) for p in pos) or 'flat'))
    return perf


if __name__ == '__main__':
    main()
