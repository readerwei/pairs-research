"""Go back for the true NBBO once the 15-minute SIP delay has passed.

    python slippage_reconcile.py                       # today's probe
    python slippage_reconcile.py --date 2026-08-18

slippage_probe records the IEX bid/ask, because that is all this account can see
in real time. IEX is one venue at roughly 2-3% of volume, and its quote can sit
well outside the national best bid and offer -- QQQ has been measured 88 cents
wide on IEX against 2 cents on SIP.

Fifteen minutes after the fact the real NBBO for those same microseconds becomes
queryable, so the honest measurement is made here, not live. For each recorded
leg this pulls the prevailing SIP quote at the submission timestamp and reports
slippage against it, alongside how far IEX was from the truth at that instant.
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

RUNS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'runs')


def prevailing(api, symbol, ts, window_s=3):
    """The last SIP quote at or before ts."""
    lo = (ts - pd.Timedelta(seconds=window_s)).strftime('%Y-%m-%dT%H:%M:%SZ')
    hi = (ts + pd.Timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    try:
        q = api.get_quotes(symbol, lo, hi, limit=1000, feed='sip').df
    except Exception as e:
        return None, str(e)[:60]
    if q.empty:
        return None, 'no quotes'
    q = q[(q.bid_price > 0) & (q.ask_price > 0)]
    q = q[q.index <= ts]
    if q.empty:
        return None, 'none at or before ts'
    r = q.iloc[-1]
    return (float(r.bid_price), float(r.ask_price)), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--window', type=int, default=3,
                    help='seconds to look back for the prevailing quote')
    args = ap.parse_args()

    date = args.date or str(pd.Timestamp.now(tz='America/New_York').date())
    path = os.path.join(RUNS, 'slippage_probe_%s.csv' % date)
    if not os.path.exists(path):
        raise SystemExit('no probe output at %s' % path)
    df = pd.read_csv(path)
    if df.empty:
        raise SystemExit('%s is empty' % path)

    config.load_alpaca_env()
    import alpaca_trade_api as tradeapi
    api = tradeapi.REST()

    now = pd.Timestamp.utcnow()
    rows = []
    for _, r in df.iterrows():
        ts = pd.Timestamp(r.submitted_utc)
        if (now - ts).total_seconds() < 15 * 60:
            continue                       # still inside the SIP delay
        nbbo, err = prevailing(api, r.symbol, ts, args.window)
        if nbbo is None:
            continue
        bid, ask = nbbo
        mid = (bid + ask) / 2.0
        sign = 1.0 if r.side == 'buy' else -1.0
        touch = ask if r.side == 'buy' else bid
        rows.append({
            'symbol': r.symbol, 'side': r.side, 'shares': r.shares,
            'fill': r.fill_price,
            'sip_spread_c': 100 * (ask - bid),
            'iex_spread_c': r.iex_spread_c,
            'slip_vs_sip_mid_c': 100 * sign * (r.fill_price - mid),
            'slip_vs_sip_touch_c': 100 * sign * (r.fill_price - touch),
            'slip_vs_sip_bps': 1e4 * sign * (r.fill_price - mid) / mid,
            'iex_mid_err_c': 100 * (r.iex_mid - mid),
            'latency_s': r.latency_s,
        })

    if not rows:
        raise SystemExit('nothing old enough to reconcile yet (SIP lags 15 min)')
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(RUNS, 'slippage_reconciled_%s.csv' % date),
               index=False)

    print('=== %s: %d legs reconciled against SIP NBBO ===\n' % (date, len(out)))
    per = out.groupby('symbol').agg(
        legs=('fill', 'size'),
        sip_spread_c=('sip_spread_c', 'median'),
        iex_spread_c=('iex_spread_c', 'median'),
        slip_touch_c=('slip_vs_sip_touch_c', 'median'),
        slip_bps=('slip_vs_sip_bps', 'median'),
        iex_err_c=('iex_mid_err_c', 'median'),
        latency_s=('latency_s', 'median'),
    ).sort_values('slip_bps', ascending=False)
    print(per.round(2).to_string())
    print('\noverall: median slippage vs SIP touch %.2f cents (%.2f bps), '
          'median fill latency %.2fs'
          % (out.slip_vs_sip_touch_c.median(), out.slip_vs_sip_bps.median(),
             out.latency_s.median()))
    print('price improvement (filled better than the touch): %.0f%% of legs'
          % (100 * (out.slip_vs_sip_touch_c < 0).mean()))
    print('\nwritten: %s'
          % os.path.join(RUNS, 'slippage_reconciled_%s.csv' % date))


if __name__ == '__main__':
    main()
