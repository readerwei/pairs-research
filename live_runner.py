"""One live execution engine for every strategy.

    python live_runner.py --list
    python live_runner.py --strategy naive_momentum --check
    python live_runner.py --strategy pingpong --once --max-seconds 300
    python live_runner.py --strategy naive_momentum --session      # a full day

Strategies live in live_strategies.py and contribute only signal logic. Anything
that can lose money by being wrong -- the paper-endpoint refusal, the pre-flight,
the conflict check, the watchdog, flattening, reconciliation -- is here, written
once, and behaves identically no matter what is being run.

Three modes:

  --check     pre-flight and exit. Arms nothing, orders nothing.
  --once      run the algorithm for --max-seconds and exit. One process, one
              attempt; this is what a supervisor should call.
  --session   run a full trading day: wait for the open, run to the close,
              restart on crash, then print the end-of-day summary.

`--session` orchestrates by launching `--once` as a subprocess rather than
looping in-process, so a crash in the engine cannot corrupt the supervisor that
is meant to notice it. That is also why the shell wrapper is four lines: session
logic belongs somewhere it can be read and tested, not in bash.
"""
from __future__ import print_function

import argparse
import os
import subprocess
import sys
import time
import warnings

import pandas as pd

warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
import live_strategies  # noqa: E402
import minute_bundle  # noqa: E402

PAPER_HOST = 'paper-api.alpaca.markets'
HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, 'logs')


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------
class Tee(object):
    """stdout to the terminal and the log at once.

    Under cron there is no terminal and everything must reach the file; run by
    hand, a file-only log makes a working session look like it did nothing.
    """

    def __init__(self, path):
        self.term = sys.stdout
        self.file = open(path, 'a')

    def write(self, s):
        self.term.write(s)
        self.file.write(s)
        self.flush()

    def flush(self):
        self.term.flush()
        self.file.flush()


def say(msg):
    print('%s  %s' % (pd.Timestamp.now(tz='America/New_York')
                      .strftime('%H:%M:%S'), msg))
    sys.stdout.flush()


# --------------------------------------------------------------------------
# session calendar
# --------------------------------------------------------------------------
def session_window(cal=None):
    """(is_session, seconds_to_open, seconds_to_close) for today."""
    cal = cal or config.nyse()
    now = pd.Timestamp.now(tz='UTC')
    today = pd.Timestamp(now.tz_convert('America/New_York').date(), tz='UTC')
    if not cal.is_session(today):
        return False, 0, 0
    o, c = cal.session_open(today), cal.session_close(today)
    return True, (o - now).total_seconds(), (c - now).total_seconds()


# --------------------------------------------------------------------------
# safety
# --------------------------------------------------------------------------
def check_conflicts(symbols, my_pid=None):
    """Refuse to trade a symbol another live runner on this box already owns.

    Two live algorithms pointed at one position do not race and settle. Each
    reads the other's fills as its own, and they undo each other for as long as
    both are up.
    """
    import glob
    my_pid = my_pid or os.getpid()
    conflicts = {}
    for path in glob.glob('/proc/[0-9]*/cmdline'):
        pid = path.split('/')[2]
        if pid == str(my_pid):
            continue
        try:
            with open(path) as f:
                cmd = f.read().replace('\x00', ' ')
        except IOError:
            continue
        if 'live_runner.py' not in cmd or '--once' in cmd:
            continue          # --once children belong to a supervisor we see
        if '--check' in cmd or '--list' in cmd:
            continue
        overlap = {s for s in symbols if (' %s ' % s) in (cmd + ' ')}
        if overlap:
            conflicts[pid] = overlap
    if conflicts:
        detail = '; '.join('pid %s trading %s' % (p, ', '.join(sorted(v)))
                           for p, v in conflicts.items())
        raise SystemExit('refusing to start: another live_runner is up -- %s.\n'
                         'Stop it, or pick symbols it is not trading.' % detail)


def preflight(strategy, symbols, params, allow_real_money, show=True):
    base_url = config.load_alpaca_env()
    is_paper = PAPER_HOST in base_url
    if show:
        print('strategy     : %s -- %s' % (strategy.name, strategy.description))
        print('params       : %s' % (params or '(defaults)'))
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
    clock = api.get_clock()

    data = minute_bundle.load()
    sess = minute_bundle.minute_sessions(data)
    resolved, missing = [], []
    for s in symbols:
        try:
            data.asset_finder.lookup_symbol(s, sess[-1])
            resolved.append(s)
        except Exception:
            missing.append(s)
    if missing:
        raise SystemExit('not in the %s bundle: %s'
                         % (minute_bundle.BUNDLE, ', '.join(missing)))

    if show:
        print('account      : %s  status=%s  blocked=%s/%s'
              % (acct.id[:8], acct.status, acct.trading_blocked,
                 acct.account_blocked))
        print('equity       : $%s   cash $%s' % (acct.equity, acct.cash))
        print('market       : %s   next open %s'
              % ('OPEN' if clock.is_open else 'CLOSED', clock.next_open))
        positions = api.list_positions()
        print('positions    : %s'
              % (', '.join('%s %s' % (p.symbol, p.qty) for p in positions)
                 or 'none'))
        print('open orders  : %d' % len(api.list_orders(status='open')))
        print('symbols      : %s' % ', '.join(resolved))
        print('sizing       : %s' % strategy.sizing(resolved, **params))
        print('overnight    : %s'
              % ('holds' if strategy.holds_overnight else
                 'flattens at exit'))
    return api, clock, resolved


# --------------------------------------------------------------------------
# instrumentation shared by every strategy
# --------------------------------------------------------------------------
def instrument(handle_data, strategy, api, symbols, heartbeat, reconcile_every,
               price_check):
    """Wrap a strategy's handle_data with the reporting every live run wants."""
    state = {'bar': 0, 'quoted': {}, 'seen': set()}

    def _reconcile(context):
        zp = {a.symbol: int(p.amount)
              for a, p in context.portfolio.positions.items() if p.amount}
        try:
            br = {p.symbol: int(float(p.qty)) for p in api.list_positions()}
        except Exception as e:
            say('   broker query failed: %s' % e)
            return
        keys = sorted(set(zp) | set(br))
        if not keys:
            say('   reconcile: both flat')
            return
        for k in keys:
            z, b = zp.get(k, 0), br.get(k, 0)
            say('   reconcile %-6s engine %+4d  broker %+4d  %s'
                % (k, z, b, 'ok' if z == b else '*** MISMATCH ***'))

    def _report_fills():
        """Compare what the strategy saw against what it actually got.

        Run a bar late deliberately: a market order has no fill price at
        submission, so intent and execution can only be compared afterwards.
        """
        try:
            orders = api.list_orders(status='closed', limit=30)
        except Exception:
            return
        for o in orders:
            if o.id in state['seen'] or o.symbol not in symbols:
                continue
            if not o.filled_avg_price:
                continue
            state['seen'].add(o.id)
            q = state['quoted'].get(o.symbol)
            fill = float(o.filled_avg_price)
            if q is None:
                say('   fill %-5s %-4s at %.4f' % (o.symbol, o.side, fill))
            else:
                say('   fill %-5s %-4s quoted %.4f  filled %.4f  '
                    'slip %+.4f (%+.3f%%)'
                    % (o.symbol, o.side, q, fill, fill - q,
                       100.0 * (fill - q) / q if q else float('nan')))

    def wrapped(context, data):
        state['bar'] += 1
        if price_check:
            _report_fills()
            for a in context.syms:
                try:
                    state['quoted'][a.symbol] = float(data.current(a, 'price'))
                except Exception:
                    pass

        handle_data(context, data)

        if heartbeat and state['bar'] % heartbeat == 0:
            say('bar %-5d %s  gross=%.2f orders=%d'
                % (state['bar'], strategy.status(context),
                   context.account.leverage, getattr(context, 'n_orders', 0)))
        if reconcile_every and state['bar'] % reconcile_every == 0:
            _reconcile(context)

    return wrapped


def flatten(api, symbols):
    """Close everything this strategy owns.

    Market orders sent outside the session are accepted and queue until the
    next open, so the positions are still there when this returns. That is the
    right behaviour -- the alternative is leaving them indefinitely -- but it
    has to be said out loud, or "flatten" reads as a lie.
    """
    pos = [p for p in api.list_positions() if p.symbol in symbols]
    if not pos:
        say('flatten: already flat')
        return
    market_open = api.get_clock().is_open
    say('flatten: closing %s'
        % ', '.join('%s %s' % (p.symbol, p.qty) for p in pos))
    for p in pos:
        # A position can vanish between listing and ordering -- an earlier
        # queued order fills, and Alpaca answers 403 "insufficient qty
        # available". Raising here propagates out of the finally: block and
        # masks whatever actually ended the run, which is what happened the
        # first time this ran.
        try:
            api.submit_order(symbol=p.symbol, qty=abs(int(float(p.qty))),
                             side='sell' if float(p.qty) > 0 else 'buy',
                             type='market', time_in_force='day')
        except Exception as e:
            say('flatten: %s failed (%s)' % (p.symbol, str(e)[:80]))
    if not market_open:
        say('flatten: market is closed -- these are queued and will fill at '
            'the next open')
        return
    time.sleep(5)
    left = [p for p in api.list_positions() if p.symbol in symbols]
    say('flatten: %s'
        % (', '.join('%s %s' % (p.symbol, p.qty) for p in left) or 'flat'))


# --------------------------------------------------------------------------
# modes
# --------------------------------------------------------------------------
def state_file(strategy, symbols):
    return os.path.join(HERE, 'state_%s_%s.pkl'
                        % (strategy.name,
                           '_'.join(s.replace('.', '') for s in symbols)))


def run_once(args, strategy, params):
    api, clock, symbols = preflight(strategy, args.symbols, params,
                                    args.allow_real_money)
    check_conflicts(symbols)

    sf = args.state_file or state_file(strategy, symbols)
    print('state file   : %s%s'
          % (sf, '' if os.path.exists(sf) else '  (new)'))
    bar_dir = args.realtime_bar_target or None
    if bar_dir and not os.path.isdir(bar_dir):
        os.makedirs(bar_dir)

    if not clock.is_open:
        nxt = pd.Timestamp(clock.next_open)
        nxt = nxt.tz_localize('UTC') if nxt.tzinfo is None else nxt.tz_convert('UTC')
        hrs = (nxt - pd.Timestamp.now(tz='UTC')).total_seconds() / 3600.0
        print('\nMARKET IS CLOSED. Next open %s (in %.1f hours). The algorithm'
              % (nxt.tz_convert('America/New_York').strftime('%a %H:%M %Z'), hrs))
        print('will idle and print nothing until then -- the heartbeat fires on')
        print('minute bars and there are none outside the session.')

    from logbook import StderrHandler, WARNING
    StderrHandler(level=WARNING).push_application()

    from zipline import run_algorithm
    from zipline.gens.brokers.alpaca_broker import ALPACABroker

    minute_bundle.register_readonly()
    broker = ALPACABroker()
    init, handle, bts = strategy.build(symbols, **params)
    handle = instrument(handle, strategy, api, symbols, args.heartbeat,
                        args.reconcile_every, not args.no_price_check)

    deadline = time.time() + args.max_seconds if args.max_seconds else None
    say('running%s' % (' for %ds' % args.max_seconds if deadline else ''))
    try:
        return run_algorithm(
            start=pd.Timestamp.utcnow().normalize() - pd.Timedelta(days=5),
            end=pd.Timestamp.utcnow().normalize() + pd.Timedelta(days=1),
            initialize=init, handle_data=handle, before_trading_start=bts,
            capital_base=float(api.get_account().equity),
            bundle=minute_bundle.BUNDLE,
            trading_calendar=config.nyse(),
            data_frequency='minute',
            broker=broker,
            state_filename=sf,
            realtime_bar_target=bar_dir,
            stop_execution_callback=(
                lambda e: bool(deadline and time.time() > deadline)),
        )
    finally:
        should_flatten = (strategy.flatten_on_exit
                          if args.flatten is None else args.flatten)
        if should_flatten:
            try:
                flatten(api, symbols)
            except Exception as e:
                say('flatten failed entirely: %s' % str(e)[:120])


def run_session(args, strategy, params):
    """A full trading day, supervising --once children."""
    log_fh = getattr(sys.stdout, 'file', None) or sys.__stdout__
    cal = config.nyse()
    is_session, to_open, to_close = session_window(cal)
    if not is_session:
        say('not an NYSE session today (weekend or holiday) -- nothing to do')
        return 0
    if to_close <= 60:
        say('today was a session but it closed %d minutes ago -- nothing to do'
            % (-to_close / 60))
        return 0
    if to_open > 0:
        say('waiting %.0f minutes for the open' % (to_open / 60.0))
        time.sleep(to_open)

    child = [sys.executable, os.path.abspath(__file__),
             '--strategy', strategy.name, '--once',
             '--symbols'] + list(args.symbols)
    for k, v in params.items():
        child += ['--param', '%s=%s' % (k, v)]
    for flag, val in (('--heartbeat', args.heartbeat),
                      ('--reconcile-every', args.reconcile_every)):
        child += [flag, str(val)]
    if args.no_price_check:
        child.append('--no-price-check')
    if args.allow_real_money:
        child.append('--allow-real-money')

    attempt = 0
    while True:
        _, _, remaining = session_window(cal)
        if remaining <= 60:
            say('%.0fs to the close -- done for today' % remaining)
            break
        attempt += 1
        say('attempt %d, running for %.0fs' % (attempt, remaining))
        # A subprocess, so a crash in the engine cannot take the supervisor with
        # it, and a hard timeout can bound a hang the watchdog missed.
        try:
            # Children inherit fd 1, not the parent's Python-level Tee, so their
            # output -- including tracebacks -- bypassed the session log
            # entirely. Hand them the file directly.
            rc = subprocess.call(child + ['--max-seconds', str(int(remaining))],
                                 stdout=log_fh, stderr=subprocess.STDOUT,
                                 timeout=remaining + 120)
        except subprocess.TimeoutExpired:
            say('child exceeded its own deadline by 120s -- killed')
            rc = -1
        say('child exited rc=%s' % rc)
        if rc == 0:
            break
        if attempt >= args.max_restarts:
            say('%d failed attempts -- giving up' % args.max_restarts)
            break
        time.sleep(30)

    say('end-of-day summary')
    subprocess.call([sys.executable, os.path.join(HERE, 'eod_summary.py'),
                     '--strategy', strategy.name,
                     '--symbols'] + list(args.symbols))
    say('session finished')
    return 0


# --------------------------------------------------------------------------
def parse_params(strategy, pairs):
    params = dict(strategy.defaults)
    for p in pairs or []:
        if '=' not in p:
            raise SystemExit('--param expects key=value, got %r' % p)
        k, v = p.split('=', 1)
        if k not in strategy.defaults:
            raise SystemExit('%s takes %s, not %r'
                             % (strategy.name,
                                ', '.join(sorted(strategy.defaults)) or 'no params',
                                k))
        default = strategy.defaults[k]
        params[k] = type(default)(v) if default is not None else v
    return params


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--list', action='store_true', help='show strategies')
    ap.add_argument('--strategy')
    ap.add_argument('--symbols', nargs='+')
    ap.add_argument('--param', action='append', metavar='KEY=VALUE')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--session', action='store_true')
    ap.add_argument('--max-seconds', type=float, default=None)
    ap.add_argument('--max-restarts', type=int, default=5)
    ap.add_argument('--heartbeat', type=int, default=15)
    ap.add_argument('--reconcile-every', type=int, default=0)
    ap.add_argument('--no-price-check', action='store_true')
    ap.add_argument('--flatten', dest='flatten', action='store_true',
                    default=None, help="override the strategy's exit policy")
    ap.add_argument('--no-flatten', dest='flatten', action='store_false')
    ap.add_argument('--state-file')
    ap.add_argument('--realtime-bar-target', default=os.path.join(HERE,
                                                                  'live_bars'))
    ap.add_argument('--log-dir', default=LOG_DIR)
    ap.add_argument('--allow-real-money', action='store_true')
    args = ap.parse_args()

    if args.list:
        for n in live_strategies.names():
            s = live_strategies.get(n)
            print('%-20s %s' % (n, s.description))
            print('%-20s defaults: %s   symbols: %s%s'
                  % ('', s.defaults or 'none', ' '.join(s.default_symbols),
                     '   [flattens on exit]' if s.flatten_on_exit else ''))
        return 0

    if not args.strategy:
        raise SystemExit('--strategy is required (see --list)')
    strategy = live_strategies.get(args.strategy)
    params = parse_params(strategy, args.param)
    if not args.symbols:
        args.symbols = list(strategy.default_symbols)
    args.symbols = [s.upper() for s in args.symbols]

    # Only the supervisor logs to file; --once children inherit its stdout.
    if args.session:
        if not os.path.isdir(args.log_dir):
            os.makedirs(args.log_dir)
        path = os.path.join(args.log_dir, '%s_%s.log'
                            % (strategy.name, pd.Timestamp.now().date()))
        sys.stdout = Tee(path)
        print('=' * 66)
        say('logging to %s' % path)

    if args.check:
        preflight(strategy, args.symbols, params, args.allow_real_money)
        check_conflicts(args.symbols)
        print('\n--check: nothing armed.')
        return 0
    if args.session:
        return run_session(args, strategy, params)
    if args.once:
        run_once(args, strategy, params)
        return 0
    raise SystemExit('pick a mode: --check, --once or --session')


if __name__ == '__main__':
    sys.exit(main() or 0)
