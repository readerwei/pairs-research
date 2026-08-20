"""Screen the research universe for tradeable pairs -- IN-SAMPLE ONLY.

    python screen_pairs.py                 # screen, write runs/<date>/candidates.csv
    python screen_pairs.py --all           # don't apply thresholds, dump every pair

For each within-group pair (A, B) this measures, over the in-sample window:

  corr        Pearson correlation of daily log returns. Co-movement is a
              necessary condition; it is not sufficient and is not the signal.
  coint_p     Engle-Granger two-step p-value. This is the statistical claim
              that a linear combination of A and B is stationary, i.e. that the
              spread has something to revert to.
  beta        OLS hedge ratio of log(A) on log(B) over the in-sample window.

              Everything here works in LOG prices, not levels. On levels the
              hedge ratio absorbs the price-level difference between the two
              names, so a $150 stock paired against a $40 one comes back with a
              beta whose dollar hedge covers a fraction of the exposure -- the
              "spread" is then mostly a directional bet on the expensive leg
              wearing a pairs costume. In logs beta is an elasticity: 1.0 means
              a 1% move in B goes with a 1% move in A, which is the thing a
              dollar-neutral book actually trades.

  half_life   OU half-life of the residual, from an AR(1) fit. Answers "how
              many sessions until a shock decays by half" -- this is what
              decides whether a spread is tradeable, not the p-value. Under ~2
              sessions is microstructure noise you can't capture after costs;
              over ~60 the position sits open through a whole quarter.
  spread_vol  Std of the log residual, i.e. roughly its size in percent.

Nothing here looks at the out-of-sample window. The whole reason the split
exists is that cointegration tests run over the full history and then backtested
over that same history will always look good.
"""
from __future__ import print_function

import argparse
import os
import sys

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import coint
from statsmodels.tsa.vector_ar.vecm import coint_johansen
from statsmodels.tsa.api import VAR

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
import data as rdata  # noqa: E402


def johansen(ya, xb, max_lag=10):
    """(trace0, trace1, hedge_ratio) from the Johansen procedure.

    Engle-Granger asks whether the residual of one specific regression is
    stationary, so it is direction-dependent -- coint(A, B) and coint(B, A) can
    disagree -- and it hands back a p-value with no vector attached. Johansen
    tests the system, is symmetric, and returns the cointegrating vector
    itself, which is the thing a hedge should actually be built from.

    trace0 tests rank 0 (no cointegration) and trace1 tests rank <= 1; a pair
    is cointegrated when trace0 clears its critical value. The hedge ratio is
    -w2/w1 from the eigenvector of the strongest relationship, normalised on
    the first leg.

    Returns (nan, nan, nan) when the fit fails -- these are numerically fragile
    on short or near-degenerate windows and must not take the screen down.
    """
    df = pd.DataFrame({'a': ya, 'b': xb})
    try:
        # Lag order from AIC rather than a hardcoded constant: the right number
        # of lags is a property of the pair. Cap it so a long window cannot
        # select an order that eats the degrees of freedom.
        k = VAR(df.values).select_order(maxlags=max_lag).selected_orders['aic']
        k = max(1, int(k))
        # VECM/Johansen uses k_ar_diff = VAR_lag_order - 1. k_ar_diff=0 is valid
        # (no lagged differences in the VECM), so we allow 0 when VAR order is 1.
        cj = coint_johansen(df.values, det_order=0, k_ar_diff=k - 1)
        w = cj.evec[:, cj.ind[0]]
        hr = float(-w[1] / w[0]) if w[0] != 0 else np.nan
        return float(cj.lr1[0]), float(cj.lr1[1]), hr
    except Exception:
        return np.nan, np.nan, np.nan


def half_life(spread):
    """OU half-life in sessions, from an AR(1) fit on the spread.

    d(spread)_t = lambda * spread_{t-1} + eps  ->  hl = -ln(2) / ln(1 + lambda)
    Returns np.inf when the fit says the spread is not mean reverting.
    """
    s = np.asarray(spread, dtype=float)
    lag = s[:-1]
    delta = np.diff(s)
    lag_c = np.column_stack([lag, np.ones(len(lag))])
    try:
        lam = np.linalg.lstsq(lag_c, delta, rcond=None)[0][0]
    except Exception:
        return np.inf
    if lam >= 0:
        return np.inf
    return float(-np.log(2) / lam)


def pair_stats(px, a, b):
    """All in-sample statistics for one pair, or None if the data is unusable."""
    sub = px[[a, b]].dropna()
    sub = sub[(sub > 0).all(axis=1)]
    if len(sub) < 250:
        return None

    ya = np.log(sub[a].values.astype(float))
    xb = np.log(sub[b].values.astype(float))
    # Tolerance instead of exact zero: constant prices give log-std ~1e-16,
    # not exactly 0.0. Anything below 1e-12 is effectively flat.
    if ya.std() < 1e-12 or xb.std() < 1e-12:
        return None

    rets = np.log(sub).diff().dropna()
    corr = float(rets[a].corr(rets[b]))

    # Engle-Granger BOTH directions. The test regresses one series on the other
    # and asks whether the residual is stationary, so it is not symmetric:
    # coint(A, B) and coint(B, A) routinely disagree, and running one arbitrary
    # direction means the screen's answer depends on symbol ordering. Take the
    # stronger of the two, as ML4T ch.9 does.
    try:
        _, p_ab, _ = coint(ya, xb)
        _, p_ba, _ = coint(xb, ya)
        pval = float(min(p_ab, p_ba))
    except Exception:
        return None

    trace0, trace1, jo_hr = johansen(ya, xb)

    beta, intercept = np.polyfit(xb, ya, 1)
    spread = ya - (beta * xb + intercept)
    sd = spread.std()

    return {
        'sym_a': a,
        'sym_b': b,
        'n_sessions': len(sub),
        'corr': corr,
        'coint_p': float(pval),
        'coint_p_ab': float(p_ab),
        'coint_p_ba': float(p_ba),
        'trace0': trace0,
        'trace1': trace1,
        'johansen_hedge': jo_hr,
        'beta': float(beta),
        'intercept': float(intercept),
        'half_life': half_life(spread),
        'spread_vol': float(sd),
        'last_z': float(spread[-1] / sd) if sd else np.nan,
    }


def screen(px, keep_all=False):
    rows = []
    for a, b, group in rdata.candidate_pairs():
        if a not in px.columns or b not in px.columns:
            continue
        st = pair_stats(px, a, b)
        if st is None:
            continue
        st['group'] = group
        st['eg_sig'] = bool(st['coint_p'] <= config.MAX_COINT_PVALUE)
        # Johansen rank test for a 2-variable system with exactly one cointegrating
        # relationship: trace0 rejects rank=0 (trace0 > cv0), trace1 fails to
        # reject rank<=1 (trace1 <= cv1). Both must hold for r=1.
        st['johansen_sig'] = bool(
            np.isfinite(st['trace0']) and
            st['trace0'] > config.JOHANSEN_TRACE0_CV and
            st['trace1'] <= config.JOHANSEN_TRACE1_CV)
        coint_ok = (st['eg_sig'] and st['johansen_sig']
                    if config.REQUIRE_BOTH_COINT_TESTS else st['eg_sig'])
        st['passes'] = bool(
            abs(st['corr']) >= config.MIN_ABS_CORR and
            coint_ok and
            config.MIN_HALF_LIFE <= st['half_life'] <= config.MAX_HALF_LIFE and
            config.MIN_BETA <= st['beta'] <= config.MAX_BETA)
        rows.append(st)

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    if not keep_all:
        df = df[df['passes']]
    # Rank by half-life first: among pairs that already passed the
    # cointegration gate, how *fast* the spread reverts is what decides whether
    # the strategy gets enough round trips to matter. p-value breaks ties.
    df = df.sort_values(['half_life', 'coint_p']).reset_index(drop=True)
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--all', action='store_true',
                    help='write every pair, not just those passing thresholds')
    ap.add_argument('--date', default=None, help='run folder name (default: today)')
    args = ap.parse_args()

    # rdata.session_range(), not config's: config computes the window from the
    # exchange calendar, which knows about today, while the bundle stops at its
    # last ingest. When the two disagree -- pairs_research ending 2026-08-14
    # while the calendar has opened 2026-08-18 -- zipline raises a bare
    # KeyError from deep inside the history loader instead of saying the bundle
    # is stale.
    start, end = rdata.session_range()
    is_start, is_end, oos_start, oos_end = config.split_sessions(start, end)
    print('in-sample     : %s -> %s' % (is_start.date(), is_end.date()))
    print('out-of-sample : %s -> %s  (held out, not read here)'
          % (oos_start.date(), oos_end.date()))

    px = rdata.close_prices(is_start, is_end)
    print('universe      : %d symbols, %d sessions' % (px.shape[1], px.shape[0]))

    df = screen(px, keep_all=args.all)
    if df.empty:
        print('\nno pair passed the screen')
        return df

    out_dir = config.run_dir(args.date)
    path = os.path.join(out_dir, 'candidates.csv')
    df.to_csv(path, index=False)

    n_tested = len(rdata.candidate_pairs())
    print('\npairs tested   : %d' % n_tested)
    print('expected false positives at p<=%.2f by chance alone: ~%.0f'
          % (config.MAX_COINT_PVALUE, n_tested * config.MAX_COINT_PVALUE))
    print('-> a pair passing this screen is a hypothesis, not a result. The '
          'out-of-sample\n   window is what decides.')

    top = df.head(config.TOP_N_PAIRS)
    print('\n%d pairs passed; top %d carried into the backtest grid:\n'
          % (len(df), len(top)))
    cols = ['sym_a', 'sym_b', 'group', 'corr', 'coint_p', 'beta',
            'half_life', 'last_z']
    with pd.option_context('display.width', 140,
                           'display.float_format', lambda v: '%8.3f' % v):
        print(top[cols].to_string(index=False))
    print('\nwritten: %s' % path)
    return df


if __name__ == '__main__':
    main()
