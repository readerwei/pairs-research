#!/bin/bash
# Post-close check: did the strategy actually run, and what did it do?
#
# Exists because a cron job that silently does nothing is indistinguishable from
# a strategy that found no signal -- both leave zero orders and an unchanged
# account. Run it after the close so a missed session is noticed the same day.
#
#   crontab -e:
#     10 15 * * 1-5 /home/wei/Documents/zipline/research/eod_check.sh
#   (15:10 America/Chicago = 16:10 New York, ten minutes after the bell)
set -uo pipefail
export ZIPLINE_TRADER_CONFIG=/home/wei/Documents/zipline-yaml/zipline-trader.yaml
# shellcheck disable=SC1091
source /home/wei/anaconda3/bin/activate alpaca
cd /home/wei/Documents/zipline/research
exec python eod_summary.py --strategy "${STRATEGY:-naive_momentum}" \
     --symbols ${SYMBOLS:-AMD GOOG UNH} --email
