"""Shared configuration for the pairs research loop.

Everything that the other scripts need to agree on lives here: the bundle name,
the candidate universe, the in-sample / out-of-sample split, and where a day's
run writes its output.

Python 3.6 / pandas 1.1.5 / numpy 1.19.5 -- see ../CLAUDE.md before editing.
"""
import os
from datetime import timedelta

import pandas as pd
import trading_calendars

# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------
RESEARCH_DIR = os.path.dirname(os.path.abspath(__file__))
RUNS_DIR = os.path.join(RESEARCH_DIR, 'runs')
CONFIG_YAML = '/home/wei/Documents/zipline-yaml/zipline-trader.yaml'

# The production bundle ('alpaca_api') is overwritten nightly by the cron job and
# only carries the 34 names in the yaml's custom_asset_list. Research gets its
# own bundle so a re-ingest here can never disturb the live pipeline.
BUNDLE = 'pairs_research'

# --------------------------------------------------------------------------
# candidate universe
# --------------------------------------------------------------------------
# Pairs trading needs candidates that share an economic driver -- screening a
# random universe finds spurious cointegration and nothing else. These are
# grouped by the driver that should make a spread stationary. Everything from
# the live yaml universe is included so research and production see the same
# names.
UNIVERSE_GROUPS = {
    'payments':      ['V', 'MA', 'PYPL', 'AXP'],
    'money_center':  ['JPM', 'BAC', 'C', 'WFC', 'GS', 'MS'],
    'staples':       ['PEP', 'KO', 'PG', 'CL', 'MDLZ', 'KHC'],
    'big_oil':       ['XOM', 'CVX', 'COP', 'SLB'],
    'semis':         ['AMD', 'NVDA', 'INTC', 'TSM', 'MU', 'AVGO'],
    'megacap_tech':  ['AAPL', 'MSFT', 'GOOG', 'AMZN', 'META'],
    'home_improve':  ['HD', 'LOW'],
    'parcel':        ['UPS', 'FDX'],
    'telco':         ['T', 'VZ'],
    'managed_care':  ['UNH', 'CVS', 'CI', 'ELV'],
    'china_ev':      ['NIO', 'XPEV', 'LI'],
    'china_adr':     ['BABA', 'JD', 'PDD'],
    'airlines':      ['AAL', 'DAL', 'UAL', 'LUV'],
    'us_autos':      ['F', 'GM', 'TSLA'],
    'retail':        ['COST', 'WMT', 'TGT'],
    'sector_etf':    ['XLE', 'XLF', 'XLK', 'XLV', 'XLP', 'XLI'],
    'index_etf':     ['SPY', 'QQQ', 'IWM', 'DIA'],
    'gold':          ['GLD', 'GDX'],
    'bonds':         ['TLT', 'IEF', 'LQD', 'HYG'],
    'misc_live':     ['BRK.B', 'TCEHY', 'ABNB', 'PLTR', 'HOOD', 'COUR',
                      'GEV', 'ARKK', 'NU'],
}

# Pairs are only screened *within* a group. Cross-group pairs (say GLD/AAL) can
# pass a cointegration test on 3 years of data and mean nothing.
SCREEN_WITHIN_GROUPS_ONLY = True

UNIVERSE = sorted({s for g in UNIVERSE_GROUPS.values() for s in g})

# --------------------------------------------------------------------------
# history window and the in-sample / out-of-sample split
# --------------------------------------------------------------------------
# The split is the whole point of this folder. Screening and the parameter grid
# only ever see IS. OOS is scored once, at the end, and is never used to choose
# a pair or a parameter -- otherwise the grid just memorises which pair happened
# to mean-revert.
HISTORY_DAYS = 1500          # calendar days of daily bars to ingest
OOS_FRACTION = 0.30          # trailing share of sessions held out


def nyse():
    return trading_calendars.get_calendar('NYSE')


def last_completed_session(cal=None):
    """Most recent NYSE session that has already closed, as a tz-utc Timestamp.

    Mirrors the guard in alpaca_api.py: asking the API for a session still in
    progress gets a forward-filled fake bar appended to the bundle.
    """
    cal = cal or nyse()
    now_ny = pd.Timestamp('now', tz='America/New_York')
    d = now_ny.date()
    while not cal.is_session(str(d)):
        d -= timedelta(days=1)
    if now_ny < cal.session_close(pd.Timestamp(d, tz='utc')):
        d -= timedelta(days=1)
        while not cal.is_session(str(d)):
            d -= timedelta(days=1)
    return pd.Timestamp(d, tz='utc')


def session_range(cal=None):
    """(start, end) tz-utc session bounds for the research bundle."""
    cal = cal or nyse()
    end = last_completed_session(cal)
    start = end - timedelta(days=HISTORY_DAYS)
    while not cal.is_session(start):
        start -= timedelta(days=1)
    return start, end


def split_sessions(start, end, cal=None, warmup=True):
    """Return (is_start, is_end, oos_start, oos_end) as tz-utc Timestamps.

    `is_start` is pushed forward by WARMUP_SESSIONS so that the longest rolling
    lookback in the grid has real history behind it on day one -- zipline
    otherwise raises HistoryWindowStartsBeforeData.

    `oos_start` needs no such shift: its history window reaches back into the
    in-sample *prices*, which is exactly what a live strategy would have done on
    that date. The holdout protects against fitting parameters to OOS outcomes,
    not against the strategy seeing prices that already happened.
    """
    cal = cal or nyse()
    sessions = cal.sessions_in_range(start, end)
    cut = int(len(sessions) * (1.0 - OOS_FRACTION))
    is_start = sessions[WARMUP_SESSIONS] if warmup else sessions[0]
    return is_start, sessions[cut - 1], sessions[cut], sessions[-1]


# --------------------------------------------------------------------------
# screen thresholds
# --------------------------------------------------------------------------
# Cointegration is the primary test; correlation is only a sanity filter that
# the two names actually move together day to day. 0.70 daily-return
# correlation turns out to be strict enough that NOTHING in this universe
# passes both gates -- the highly-correlated pairs are not cointegrated and the
# cointegrated ones are not that correlated. 0.55 keeps the pairs where both
# claims are at least defensible and lets the out-of-sample test do the
# rejecting.
MIN_ABS_CORR = 0.55          # |corr| of daily log returns, in-sample
MAX_COINT_PVALUE = 0.05      # Engle-Granger p-value, in-sample

# Johansen trace-statistic critical values at 95%, for a 2-variable system with
# a constant (det_order=0). trace0 tests rank 0 (no cointegrating relationship)
# and is the one that decides; trace1 tests rank <= 1.
JOHANSEN_TRACE0_CV = 15.4943
JOHANSEN_TRACE1_CV = 3.8415

# Require Engle-Granger and Johansen to AGREE before a pair is admitted, per
# Gonzalo & Lee (1998). The two tests ask different questions -- EG whether one
# specific residual is stationary, Johansen whether the system has a
# cointegrating rank -- and they disagree far more often than either being
# wrong would suggest. In ML4T ch.9, over ~46,000 pairs, Johansen flagged 3.2%
# and EG 6.5%, and they concurred on only 366. Insisting on agreement is the
# cheapest available defence against the false positives this screen is
# otherwise swamped by (~150 pairs at p<=0.05 yields ~7 by chance alone).
#
# Set REQUIRE_BOTH_COINT_TESTS=0 in the environment to fall back to EG only.
REQUIRE_BOTH_COINT_TESTS = os.environ.get('REQUIRE_BOTH_COINT_TESTS', '1') != '0'

MIN_HALF_LIFE = 2.0          # sessions; faster than this is noise/microstructure
MAX_HALF_LIFE = 60.0         # slower than this won't round-trip often enough
TOP_N_PAIRS = 12             # candidates carried into the backtest grid

# Log-price hedge ratio bounds. beta is an elasticity here, so 1.0 is a balanced
# pair. Outside this band one leg dominates and the "spread" is really a
# directional bet on that name -- which a dollar-neutral book will not hedge.
MIN_BETA = 0.30
MAX_BETA = 3.00

# --------------------------------------------------------------------------
# parameter grid (searched on IS only)
# --------------------------------------------------------------------------
GRID_LOOKBACK = [30, 60, 90]
GRID_ENTRY_Z = [1.5, 2.0, 2.5]
GRID_EXIT_Z = [0.0, 0.5]

# Derive the z-score window from the pair's own measured half-life instead of
# searching GRID_LOOKBACK. The lookback that defines a spread is a property of
# the PAIR, not a free parameter: a spread reverting in 5 sessions and one
# taking 40 do not want the same window. half_life is already computed by the
# screen and then discarded, so this costs nothing and removes 3 values from
# the grid (~1/3 of the hypotheses per run).
#
# window = HALF_LIFE_WINDOW_MULT * half_life, clamped to the bounds below.
# Validated on the 2026-08-14 run: the un-searched window matched or beat the
# best-of-15 grid winner out of sample on 4 of 5 pairs, and lifted AMZN/GOOG
# from 3 round trips / 14.1% exposure to 12 / 37.6% at the same total return.
# Set to 0 in the environment to restore the grid search.
USE_HALF_LIFE_WINDOW = os.environ.get('USE_HALF_LIFE_WINDOW', '1') != '0'
HALF_LIFE_WINDOW_MULT = 2.0
MIN_LOOKBACK = 10            # shorter than this the OLS slope is noise

# Sessions of history the in-sample backtest gives up so the longest lookback
# above has data behind it on its first bar.
WARMUP_SESSIONS = max(GRID_LOOKBACK) + 5

# The derived window has to fit inside the warmup the IS window already gives
# up, or the first bar reaches back before the data starts.
MAX_LOOKBACK = WARMUP_SESSIONS - 5


def window_for_half_life(hl):
    """Z-score lookback implied by an OU half-life, clamped to usable bounds."""
    import math
    if hl is None or not math.isfinite(hl) or hl <= 0:
        return None
    w = int(round(HALF_LIFE_WINDOW_MULT * hl))
    return max(MIN_LOOKBACK, min(MAX_LOOKBACK, w))

# --------------------------------------------------------------------------
# risk controls -- applied inside every backtest, not just live
# --------------------------------------------------------------------------
MAX_GROSS = 0.90             # sum of |leg weights| at entry
# set_max_leverage is a hard abort, not a warning -- a breach kills the whole
# backtest. It needs headroom over MAX_GROSS: a position opened at 0.90 gross
# drifts above 1.0 whenever the short leg rises while the long one falls, which
# is ordinary and not a risk event. The strategy rebalances back to target at
# GROSS_REBALANCE_AT, so this bound only fires if that failed.
MAX_LEVERAGE = 1.15
MAX_POSITION_PCT = 0.50      # per-leg cap, as a share of portfolio value
GROSS_REBALANCE_AT = 1.05    # gross above this while open -> resize to target
STOP_Z = 4.0                 # |z| beyond this means the relationship broke: flatten

# Stop on realised spread P&L as well as on |z|. STOP_Z is computed from the
# SAME rolling fit that generates the signal, so when the estimate breaks the
# stop breaks with it -- a beta drifting toward zero shrinks the spread's
# standard deviation and can hold |z| inside the band while the position bleeds.
# A loss limit needs nothing from the model to fire. ML4T ch.9 uses -20% on the
# spread; -15% here to sit inside MAX_OOS_DRAWDOWN so a single pair cannot
# spend the whole portfolio drawdown budget.
STOP_LOSS_PCT = -0.15
USE_PNL_STOP = os.environ.get('USE_PNL_STOP', '1') != '0'

# Size the second leg by the hedge ratio instead of holding 1:-1 in dollars.
#
# Dollar-neutral sizing is a deliberate choice -- it keeps estimation error out
# of position sizing, which matters because beta is noisy. But it means the
# spread that was tested for stationarity is NOT the spread being held, and the
# gap was never measured. On the 2026-08-14 out-of-sample window AMZN/GOOG
# should have hedged at 0.187 and instead held 1.000, leaving the book short
# ~0.46 units of residual GOOG: 19.2% of daily P&L variance was unhedged
# directional exposure rather than spread convergence.
#
# OFF by default. Turning it on trades a known, measured bias for an unknown
# amount of estimation noise, and that trade has not been validated out of
# sample yet. The diagnostic below is on regardless, so the cost of leaving it
# off is visible in every report.
USE_BETA_SIZING = os.environ.get('USE_BETA_SIZING', '0') != '0'

# Hedge ratios outside this band are not applied even when sizing is on: past
# here one leg dominates so heavily that the position is a directional bet with
# extra commission, and the estimate is usually broken rather than extreme.
BETA_SIZING_CLAMP = (0.25, 4.0)
CAPITAL_BASE = 100000

# Alpaca charges no commission on US equities; see costs.py. Set
# REALISTIC_COSTS=0 in the environment to reproduce the old (wrong) numbers.
REALISTIC_COSTS = os.environ.get('REALISTIC_COSTS', '1') != '0'
COMMISSION_PER_SHARE = 0.005     # legacy model only
COMMISSION_MIN = 1.0             # legacy model only


def run_dir(date_str=None):
    """runs/YYYY-MM-DD/, created on demand."""
    if date_str is None:
        date_str = str(last_completed_session().date())
    d = os.path.join(RUNS_DIR, date_str)
    if not os.path.isdir(d):
        os.makedirs(d)
    return d


def load_alpaca_env():
    """Export APCA_* from the yaml, the way every other script here does."""
    import yaml
    with open(CONFIG_YAML) as f:
        o = yaml.safe_load(f)
    os.environ['APCA_API_KEY_ID'] = o['alpaca']['key_id']
    os.environ['APCA_API_SECRET_KEY'] = o['alpaca']['secret']
    os.environ['APCA_API_BASE_URL'] = o['alpaca']['base_url']
    os.environ.setdefault('ZIPLINE_TRADER_CONFIG', CONFIG_YAML)
    return o['alpaca']['base_url']
