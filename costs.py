"""Alpaca's actual trading costs, measured rather than assumed.

The earlier backtests used PerShare($0.005, $1 minimum), which is an
interactive-broker-style schedule and simply wrong for this account: **Alpaca
charges no commission on US equities.** What you actually pay is

  1. the spread, because these are market orders and they cross it,
  2. the SEC Section 31 fee, on sells only,
  3. the FINRA Trading Activity Fee, on sells only.

That mis-modelling was not a rounding error. A $1 minimum on a $2,879 position
is 35 basis points a side; the measured half-spread on those names is closer to
2. The old model overstated the cost of small positions by more than an order of
magnitude, and every "commission dominates" conclusion drawn from it needs
re-testing.

Spreads
-------
Measured from Alpaca's own SIP (full NBBO) quote feed -- 1,250 quotes per
symbol, five sessions, five times of day, 2026-08-11 to 2026-08-17. Note the
IEX feed shows QQQ at 88 cents wide against SIP's 4 cents; IEX is one venue's
book, not the national best bid and offer, and using it would overstate costs
by two orders of magnitude.

  symbol   spread (bps)   half-spread paid per side
  SPY          0.13              0.06
  QQQ          0.28              0.14
  GOOG         1.75              0.88
  UNH          3.30              1.65
  AMD          3.86              1.93
  AAL          6.65              3.32
  F            7.12              3.56
  NIO         21.91             10.95

The range is the point: NIO costs 78x what QQQ does to trade, purely in spread.

Regulatory fees (2026 rates)
----------------------------
  SEC Section 31: $20.60 per $1,000,000 of sale proceeds, from 2026-04-04
  FINRA TAF:      $0.000195 per share sold, capped at $8.30 per trade

Both are charged on sells only, which is why they are a commission model here
and not part of the spread.
"""
from __future__ import print_function

from zipline.finance.commission import EquityCommissionModel
from zipline.finance.slippage import LiquidityExceeded, SlippageModel

# half-spread in basis points, per side, from the measurement above
HALF_SPREAD_BPS = {
    'SPY': 0.06, 'QQQ': 0.14, 'GOOG': 0.88, 'UNH': 1.65, 'AMD': 1.93,
    'AAL': 3.32, 'F': 3.56, 'NIO': 10.95,
}
# Anything unmeasured. The measured large caps sit near 1-2bp; 2.5 is a little
# pessimistic on purpose, since an unmeasured name is more likely to be thinner
# than SPY than tighter than it.
DEFAULT_HALF_SPREAD_BPS = 2.5

SEC_FEE_PER_DOLLAR = 20.60 / 1e6      # sells only
FINRA_TAF_PER_SHARE = 0.000195        # sells only
FINRA_TAF_CAP = 8.30                  # per trade


class SpreadSlippage(SlippageModel):
    """Cross the spread, per symbol, with a volume cap.

    zipline's FixedBasisPointsSlippage applies one rate to every equity, which
    would charge QQQ what NIO costs or vice versa -- a 78x error on a portfolio
    holding both. This looks the rate up per symbol.
    """

    def __init__(self, half_spread_bps=None, default_bps=None,
                 volume_limit=0.1):
        super(SpreadSlippage, self).__init__()
        self.half_spread_bps = dict(half_spread_bps or HALF_SPREAD_BPS)
        self.default_bps = (DEFAULT_HALF_SPREAD_BPS if default_bps is None
                            else default_bps)
        self.volume_limit = volume_limit

    def bps_for(self, asset):
        return self.half_spread_bps.get(asset.symbol, self.default_bps)

    def __repr__(self):
        return ('SpreadSlippage(symbols=%d, default=%.2fbps, volume_limit=%s)'
                % (len(self.half_spread_bps), self.default_bps,
                   self.volume_limit))

    def process_order(self, data, order):
        volume = data.current(order.asset, 'volume')
        max_volume = int(self.volume_limit * volume)
        price = data.current(order.asset, 'close')
        shares_to_fill = min(abs(order.open_amount),
                             max_volume - self.volume_for_bar)
        if shares_to_fill == 0:
            raise LiquidityExceeded()
        pct = self.bps_for(order.asset) / 10000.0
        # buys lift the offer, sells hit the bid
        return (price + price * (pct * order.direction),
                shares_to_fill * order.direction)


class AlpacaFees(EquityCommissionModel):
    """Zero commission; SEC Section 31 and FINRA TAF on sells only."""

    def __init__(self, sec_fee=SEC_FEE_PER_DOLLAR, taf=FINRA_TAF_PER_SHARE,
                 taf_cap=FINRA_TAF_CAP):
        self.sec_fee = sec_fee
        self.taf = taf
        self.taf_cap = taf_cap

    def __repr__(self):
        return ('AlpacaFees(commission=0, sec=$%.2f/M on sells, '
                'taf=$%g/share on sells)' % (self.sec_fee * 1e6, self.taf))

    def calculate(self, order, transaction):
        if transaction.amount >= 0:
            return 0.0                      # buys are free
        shares = abs(transaction.amount)
        proceeds = shares * transaction.price
        return proceeds * self.sec_fee + min(shares * self.taf, self.taf_cap)


def apply(context=None):
    """Install both models. Call from initialize()."""
    from zipline.api import set_commission, set_slippage
    set_slippage(SpreadSlippage())
    set_commission(AlpacaFees())
