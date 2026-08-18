#!/bin/bash
# Thin launcher. All session logic -- calendar, waiting for the open, running to
# the close, restart-on-crash, logging, the end-of-day summary -- lives in
# live_runner.py, where it can be read and tested. This exists only because cron
# needs a conda environment and a working directory.
#
#   crontab -e:
#     ZIPLINE_TRADER_CONFIG=/home/wei/Documents/zipline-yaml/zipline-trader.yaml
#     25 8 * * 1-5 /home/wei/Documents/zipline/research/run_live.sh --strategy naive_momentum
set -euo pipefail
export ZIPLINE_TRADER_CONFIG=/home/wei/Documents/zipline-yaml/zipline-trader.yaml
# shellcheck disable=SC1091
source /home/wei/anaconda3/bin/activate alpaca
cd /home/wei/Documents/zipline/research
exec python live_runner.py --session "$@"
