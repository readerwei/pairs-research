"""Make pyfolio 0.9.2 work on pandas 1.x.

    import pyfolio_compat        # patches on import
    import pyfolio

pyfolio's last release is 0.9.2 (2019) and it predates pandas 1.0. The
maintained fork (pyfolio-reloaded) requires Python >= 3.7, so it is not an
option in the 3.6 `alpaca` env -- pip reports no distribution at all. Rather
than give up the tear sheets, patch the one thing that actually broke.

The break
---------
`get_max_drawdown_underwater` locates the drawdown valley with
`np.argmin(underwater)`. Through pandas 0.x that returned the index *label*
(a Timestamp); pandas 1.0 aligned Series.argmin with numpy and made it return
the *position* (an int64). Two things then go wrong downstream:

  * `valley.to_pydatetime()` raises AttributeError -- an int has no such method,
    which is the error you see;
  * `underwater[:valley]` silently changes meaning from label-slicing to
    positional slicing, so even a naive fix to the first problem would compute
    the peak from the wrong window.

`Series.idxmin()` has meant "the label of the minimum" in every pandas version,
so it fixes both at once.

Install the two packages with:
    pip install --no-deps pyfolio==0.9.2 empyrical==0.5.5 seaborn==0.11.2
(--no-deps matters: pyfolio's pins would try to drag pandas backwards and take
zipline-trader down with it.)
"""
from __future__ import print_function

import numpy as np


def _get_max_drawdown_underwater(underwater):
    """Peak, valley and recovery labels for the deepest drawdown."""
    valley = underwater.idxmin()          # was np.argmin(underwater)
    peak = underwater[:valley][underwater[:valley] == 0].index[-1]
    try:
        recovery = underwater[valley:][underwater[valley:] == 0].index[0]
    except IndexError:
        recovery = np.nan                 # never recovered
    return peak, valley, recovery


def fix_date_axes(fig):
    """Re-fit every date x-axis in a pyfolio figure to the data actually plotted.

    Without this, every time-series panel of a tear sheet renders *blank* on
    matplotlib 3.3: correct axes, correct y-range, correct titles, no lines.

    Cause: pyfolio calls `ax.set_xlim((returns.index[0], returns.index[-1]))`
    with tz-aware pandas Timestamps, and the tear sheet's panels share an x-axis.
    The resulting view window lands nowhere near the converted line data -- for a
    2026 backtest the axes came back showing roughly 1999 to 2010 -- so every
    curve is drawn far outside the visible area. The plot is not empty; you are
    looking at the wrong decade.

    Fixing it by hand is safe because matplotlib already tracks the union of
    every artist's extent in `ax.dataLim`, which is populated correctly. This
    just points the view at it.

    Silent failure of exactly this kind is why a chart should be looked at
    before it is sent to anyone: the numbers in the stats table were right the
    whole time.

    Second problem, same family: the tear sheet's panels are created with
    `sharex`, and on this matplotlib the shared-axis machinery leaves every
    panel -- including the bottom one -- with no x tick labels at all. The
    locator and formatter are fine (they produce '2026-05-01' when asked
    directly); the Text artists are simply not there. Forcing `labelbottom`
    brings the dates back.
    """
    import matplotlib.dates as mdates
    import numpy as np

    for ax in fig.axes:
        # only touch axes whose units are dates; a bar chart's x is categorical
        converter = getattr(ax.xaxis, 'converter', None)
        if not isinstance(converter, mdates.DateConverter):
            continue
        x0, x1 = ax.dataLim.intervalx
        if np.isfinite(x0) and np.isfinite(x1) and x1 > x0:
            pad = (x1 - x0) * 0.01
            ax.set_xlim(x0 - pad, x1 + pad)
        ax.xaxis.set_tick_params(labelbottom=True, which='both')
        for lbl in ax.get_xticklabels():
            lbl.set_visible(True)
    return fig


def _patch_rolling_windows():
    """Make the rolling volatility / Sharpe panels adapt to short samples.

    pyfolio hardcodes a six-month (126 session) rolling window in
    `create_returns_tear_sheet`. Score a 75-session holdout with it and every
    value is NaN, so both panels render empty -- which looks like the same
    off-screen bug as above but is not: there is genuinely nothing to draw.

    A window that cannot fit is replaced with one third of the sample (floor
    21 sessions, i.e. a month) and the panel title is corrected to say what was
    actually computed. Samples long enough for six months are untouched.
    """
    from pyfolio import plotting
    if getattr(plotting, '_adaptive_window_patched', False):
        return
    from pyfolio.utils import APPROX_BDAYS_PER_MONTH

    default = APPROX_BDAYS_PER_MONTH * 6

    def _window_for(returns):
        n = len(returns)
        if n >= default + 5:
            return default, '6-month'
        w = max(APPROX_BDAYS_PER_MONTH, n // 3)
        return w, '%d-session' % w

    def _wrap(name):
        original = getattr(plotting, name)

        def wrapper(returns, *args, **kwargs):
            if kwargs.get('rolling_window') is None:
                w, label = _window_for(returns)
                kwargs['rolling_window'] = w
            else:
                label = None
            ax = original(returns, *args, **kwargs)
            if label and ax is not None:
                ax.set_title(ax.get_title().replace('(6-month)',
                                                    '(%s)' % label))
            return ax
        wrapper.__name__ = name
        setattr(plotting, name, wrapper)

    for fn in ('plot_rolling_volatility', 'plot_rolling_sharpe'):
        _wrap(fn)
    plotting._adaptive_window_patched = True


def apply():
    """Patch pyfolio in place. Safe to call more than once."""
    from pyfolio import timeseries as pts
    if getattr(pts, '_pandas1_patched', False):
        return False
    pts.get_max_drawdown_underwater = _get_max_drawdown_underwater
    pts._pandas1_patched = True
    _patch_rolling_windows()
    return True


apply()
