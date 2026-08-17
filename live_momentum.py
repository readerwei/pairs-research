"""Run the naive momentum strategy live against Alpaca.

    python live_momentum.py --symbols AMD GOOG UNH --check     # pre-flight only
    python live_momentum.py --symbols AMD GOOG UNH --max-seconds 300
    python live_momentum.py --symbols AMD GOOG UNH             # run the session

Read this before running it
---------------------------
The AMD/GOOG/UNH basket was chosen by ranking a year of data and taking the
best. It is rank 1 of all 5456 possible 3-name baskets in this universe, against
a median Sharpe of 0.31. The honest estimate of this strategy's edge is the
walk-forward number -- Sharpe 1.11 for a top-3 selection, and 0.24 once the one
dominant name is removed -- not the 3.67 the chosen basket shows in hindsight.

Nothing here prevents you from trading it. The guards below are about not losing
money by *accident*; whether the strategy is worth trading at all is a separate
question, answered in runs/<date>-momentum/report.md.

Safety
------
* Refuses any endpoint that is not Alpaca paper unless --allow-real-money is
  passed explicitly.
* --check does a full pre-flight (account, positions, clock, symbol resolution)
  and exits without arming anything.
* --max-seconds stops the run cleanly after N seconds, for smoke tests.
* Regular hours only. The Alpaca broker submits market orders with no
  `extended_hours` flag, so Alpaca defaults it off, and the NYSE calendar only
  ticks the algorithm between 09:31 and 16:00 ET. Note that this differs from
  dailyExecute.py, which deliberately sends `extended_hours=True` limit orders.
* The leverage cap is armed from before_trading_start, so it is active live --
  see the note in momentum.py about initialize() never being called.

This is zipline-trader's own live machinery, not a reimplementation of it
-------------------------------------------------------------------------
Passing `broker=` to run_algorithm is the documented way in; run_algo.py then
swaps `TradingAlgorithm` for `LiveTradingAlgorithm` and `DataPortal` for
`DataPortalLive`, and forces the emission rate to minute. Verified by
instrumenting a live run: LiveTradingAlgorithm / DataPortalLive / ALPACABroker.

The `zipline run --broker alpaca --state-file ...` CLI reaches the same code, and
is the right entry point for a fixed algorithm in a file. It is not usable here
because the strategy is parameterised -- symbols, N, gross -- and the CLI has no
way to pass arguments through to the algorithm. Hence run_algorithm directly,
which also leaves room for the paper-endpoint refusal and the pre-flight.

State
-----
Position and counter state persists to --state-file. The consecutive-bar counter
survives a restart, which matters: a fresh counter starts at zero and cannot
fire until it has seen N bars in one direction.
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
import momentum  # noqa: E402

PAPER_HOST = 'paper-api.alpaca.markets'


def preflight(symbols, allow_real_money):
    """Everything checkable before a single order can be sent."""
    base_url = config.load_alpaca_env()
    is_paper = PAPER_HOST in base_url
    print('endpoint     : %s  (%s)'
          % (base_url, 'PAPER' if is_paper else 'REAL MONEY'))
    if not is_paper and not allow_real_money:
        raise SystemExit(
            'refusing to run: %s is not the Alpaca paper endpoint.\n'
            'If that is genuinely what you want, pass --allow-real-money.'
            % base_url)

    import alpaca_trade_api as tradeapi
    api = tradeapi.REST()

    acct = api.get_account()
    print('account      : %s  status=%s' % (acct.id[:8], acct.status))
    print('equity       : $%s   cash $%s' % (acct.equity, acct.cash))
    print('blocked      : trading=%s account=%s'
          % (acct.trading_blocked, acct.account_blocked))

    clock = api.get_clock()
    print('market       : %s   next open %s / next close %s'
          % ('OPEN' if clock.is_open else 'CLOSED',
             clock.next_open, clock.next_close))

    positions = api.list_positions()
    if positions:
        print('positions    :')
        for p in positions:
            print('   %-6s qty %-8s  mkt $%-12s  unrealised %s%%'
                  % (p.symbol, p.qty, p.market_value,
                     round(float(p.unrealized_plpc) * 100, 2)))
    else:
        print('positions    : none')

    open_orders = api.list_orders(status='open')
    print('open orders  : %d' % len(open_orders))

    data = minute_bundle.load()
    sess = minute_bundle.minute_sessions(data)
    resolved, missing = [], []
    for s in symbols:
        try:
            data.asset_finder.lookup_symbol(s, sess[-1])
            resolved.append(s)
        except Exception:
            missing.append(s)
    print('symbols      : %s' % ', '.join(resolved))
    if missing:
        raise SystemExit('not in the %s bundle: %s'
                         % (minute_bundle.BUNDLE, ', '.join(missing)))

    weight = momentum.MAX_GROSS / float(len(resolved))
    print('sizing       : %.1f%% of equity per name when long, %.1f%% gross if '
          'all %d are long' % (weight * 100, momentum.MAX_GROSS * 100,
                               len(resolved)))
    print('leverage cap : %.2f' % momentum.MAX_LEVERAGE)
    return api, clock, resolved


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--symbols', nargs='+', default=['AMD', 'GOOG', 'UNH'])
    ap.add_argument('--n', type=int, default=8,
                    help='consecutive same-direction minute bars that fire')
    ap.add_argument('--gross', type=float, default=None,
                    help='total gross exposure when every name is long')
    ap.add_argument('--state-file', default=None)
    ap.add_argument('--max-seconds', type=float, default=None,
                    help='stop cleanly after N seconds (smoke test)')
    ap.add_argument('--realtime-bar-target', default='./live_bars',
                    help='directory where live minute bars are written; these '
                         'are what let you reconcile the live feed against the '
                         'bundle afterwards. Pass "" to disable.')
    ap.add_argument('--check', action='store_true',
                    help='pre-flight only; place nothing, arm nothing')
    ap.add_argument('--allow-real-money', action='store_true')
    args = ap.parse_args()

    syms = [s.upper() for s in args.symbols]
    if args.gross is not None:
        momentum.MAX_GROSS = args.gross

    print('=== naive momentum N=%d on %s ===' % (args.n, '/'.join(syms)))
    api, clock, resolved = preflight(syms, args.allow_real_money)

    state_file = args.state_file or ('./live_momentum_%s.state'
                                     % '_'.join(s.replace('.', '') for s in resolved))
    print('state file   : %s%s'
          % (state_file, '' if os.path.exists(state_file) else '  (new)'))
    bar_target = args.realtime_bar_target or None
    if bar_target and not os.path.isdir(bar_target):
        os.makedirs(bar_target)
    print('live bars    : %s' % (bar_target or 'not recorded'))

    if args.check:
        print('\n--check: pre-flight only, nothing armed.')
        return

    if not clock.is_open:
        print('\nMarket is closed. The algorithm will idle until the next open;'
              '\nuse --max-seconds if you only meant to smoke-test the wiring.')

    from zipline import run_algorithm
    from zipline.gens.brokers.alpaca_broker import ALPACABroker

    minute_bundle.register_readonly()
    broker = ALPACABroker()

    deadline = time.time() + args.max_seconds if args.max_seconds else None

    def stop_execution_callback(execution_id):
        if deadline and time.time() > deadline:
            print('\n[watchdog] --max-seconds reached, stopping cleanly')
            return True
        return False

    init, handle, bts = momentum.make_naive_momentum_algo(resolved, args.n)

    start = pd.Timestamp.utcnow().normalize() - pd.Timedelta(days=5)
    end = pd.Timestamp.utcnow().normalize() + pd.Timedelta(days=1)

    print('\nstarting live loop (Ctrl-C to stop)\n')
    return run_algorithm(
        start=start, end=end,
        initialize=init, handle_data=handle, before_trading_start=bts,
        capital_base=float(api.get_account().equity),
        bundle=minute_bundle.BUNDLE,
        trading_calendar=config.nyse(),
        data_frequency='minute',
        broker=broker,
        state_filename=state_file,
        realtime_bar_target=bar_target,
        stop_execution_callback=stop_execution_callback,
    )


if __name__ == '__main__':
    main()
