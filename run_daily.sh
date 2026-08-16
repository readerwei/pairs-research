#!/bin/bash
# Daily pairs research pass: ingest -> screen -> backtest grid -> report.
#
# Independent of /home/wei/dailyexec/daily_ingest.sh. That job feeds live
# trading and must not be delayed by research; this one writes only into
# research/runs/<date>/ and never places an order.
#
# Suggested crontab (after the live pipeline has finished):
#   ZIPLINE_TRADER_CONFIG=/home/wei/Documents/zipline-yaml/zipline-trader.yaml
#   10 22 * * 0-4 /home/wei/Documents/zipline/research/run_daily.sh >> /tmp/pairs_research.log 2>&1
set -euo pipefail

RESEARCH_DIR=/home/wei/Documents/zipline/research
export ZIPLINE_TRADER_CONFIG=/home/wei/Documents/zipline-yaml/zipline-trader.yaml

# shellcheck disable=SC1091
source /home/wei/anaconda3/bin/activate alpaca
cd "$RESEARCH_DIR"

echo "=== $(date '+%F %T') pairs research ==="

echo "--- ingest ---"
python ingest_research.py

echo "--- screen (in-sample only) ---"
python screen_pairs.py

echo "--- backtest grid + out-of-sample ---"
python backtest.py

echo "--- report ---"
python report.py

echo "=== done $(date '+%F %T') ==="
