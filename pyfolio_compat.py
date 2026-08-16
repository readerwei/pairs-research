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


def apply():
    """Patch pyfolio in place. Safe to call more than once."""
    from pyfolio import timeseries as pts
    if getattr(pts, '_pandas1_patched', False):
        return False
    pts.get_max_drawdown_underwater = _get_max_drawdown_underwater
    pts._pandas1_patched = True
    return True


apply()
