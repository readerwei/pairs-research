"""End-of-day summary for the live momentum account.

    python eod_summary.py                        # today
    python eod_summary.py --date 2026-08-17
    python eod_summary.py --symbols AMD GOOG UNH --email

Answers the questions worth asking every evening: did it trade, what did it
fill at, what is it holding overnight, and does the engine's view still match
the broker's.

That last one is the reason this exists rather than just reading the Alpaca web
UI. zipline's live portfolio is a projection of the broker that nothing ever
re-syncs, so a divergence is silent. Checking the state file against the
account once a day is the cheapest way to notice.
"""
from __future__ import print_function

import argparse
import os
import sys
import warnings
from datetime import timedelta

import pandas as pd

warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402


def state_path(symbols):
    return './live_momentum_%s.state' % '_'.join(s.replace('.', '')
                                                 for s in symbols)


def summarize(symbols, date_str=None):
    config.load_alpaca_env()
    import alpaca_trade_api as tradeapi
    api = tradeapi.REST()

    day = pd.Timestamp(date_str) if date_str else pd.Timestamp.now(
        tz='America/New_York').normalize().tz_localize(None)
    lines = []
    add = lines.append

    acct = api.get_account()
    add('=== %s  live momentum: %s ===' % (day.date(), '/'.join(symbols)))
    add('equity        $%s   cash $%s' % (acct.equity, acct.cash))
    add('last equity   $%s   -> whole-account day P&L $%.2f'
        % (acct.last_equity, float(acct.equity) - float(acct.last_equity)))
    add('              (the ACCOUNT, not this strategy -- anything else trading')
    add('               this Alpaca account is in that number too)')

    start = day.tz_localize('America/New_York')
    orders = [o for o in api.list_orders(status='all', limit=500,
                                         after=start.isoformat())
              if o.symbol in symbols]
    filled = [o for o in orders if o.status == 'filled']
    add('')
    add('orders        %d submitted, %d filled, %d other'
        % (len(orders), len(filled), len(orders) - len(filled)))
    if filled:
        add('')
        add('  %-9s %-5s %-4s %-5s %-10s' % ('time', 'sym', 'side', 'qty',
                                             'fill'))
        for o in sorted(filled, key=lambda x: x.filled_at):
            add('  %-9s %-5s %-4s %-5s %-10s'
                % (o.filled_at.tz_convert('America/New_York').strftime('%H:%M:%S'),
                   o.symbol, o.side, o.qty, o.filled_avg_price))

    positions = {p.symbol: p for p in api.list_positions()
                 if p.symbol in symbols}
    add('')
    if positions:
        add('holding overnight:')
        for s, p in sorted(positions.items()):
            add('  %-5s qty %-6s  avg %-10s  last %-10s  unrealised %s%%'
                % (s, p.qty, p.avg_entry_price, p.current_price,
                   round(float(p.unrealized_plpc) * 100, 2)))
    else:
        add('holding overnight: flat')

    # engine vs broker
    path = state_path(symbols)
    add('')
    if not os.path.exists(path):
        add('state file    %s missing -- the algorithm starts cold tomorrow'
            % path)
    else:
        st = pd.read_pickle(path)
        # fromtimestamp, not Timestamp(..., unit='s'): the latter reads the
        # mtime as UTC and subtracting local now() reports a negative age.
        age = pd.Timestamp.now() - pd.Timestamp.fromtimestamp(
            os.path.getmtime(path))
        add('state file    %s (written %s ago)'
            % (path, str(age).split('.')[0]))
        cons = {a.symbol: v for a, v in st.get('cons', {}).items()}
        tgt = {a.symbol: v for a, v in st.get('target', {}).items()}
        add('  counters    %s' % cons)
        add('  orders      %s   blocked %s'
            % (st.get('n_orders'), st.get('n_blocked')))
        add('')
        add('  reconciliation (engine intent vs broker):')
        ok = True
        for s in symbols:
            want_long = bool(tgt.get(s))
            held = s in positions and float(positions[s].qty) != 0
            flag = 'ok' if want_long == held else '*** MISMATCH ***'
            if want_long != held:
                ok = False
            add('    %-5s target %-5s broker %-5s %s'
                % (s, 'long' if want_long else 'flat',
                   'long' if held else 'flat', flag))
        if not ok:
            add('')
            add('  A mismatch means the engine and the account disagree about')
            add('  what is held. Nothing re-syncs them; stop the algorithm,')
            add('  square the account by hand, delete the state file, restart.')

    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', nargs='+', default=['AMD', 'GOOG', 'UNH'])
    ap.add_argument('--date', default=None)
    ap.add_argument('--email', action='store_true',
                    help='send via the SMTP settings in the yaml')
    args = ap.parse_args()

    text = summarize([s.upper() for s in args.symbols], args.date)
    print(text)

    if args.email:
        sys.path.insert(0, '/home/wei/Documents/zipline')
        from dailyReport import email_settings
        import smtplib
        from email.mime.text import MIMEText
        cfg = email_settings()
        msg = MIMEText('<pre>%s</pre>' % text, 'html')
        msg['SUBJECT'] = 'live momentum EOD %s' % pd.Timestamp.now().date()
        s = smtplib.SMTP_SSL(cfg['host'], cfg['port'])
        s.ehlo()
        s.login(cfg['user'], cfg['password'])
        s.sendmail(cfg['user'], cfg['to'], msg.as_string())
        s.close()
        print('\nemailed to %s' % cfg['to'])


if __name__ == '__main__':
    main()
