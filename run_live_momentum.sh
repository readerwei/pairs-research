#!/bin/bash
# Production wrapper for live_momentum.py: one session per trading day.
#
# Cron fires this well before the open; the script itself decides whether today
# is a session, waits for the bell, runs until the close, and stops. Putting
# that logic here rather than in the crontab means holidays, early closes and
# daylight saving are handled by the exchange calendar instead of by a fixed
# local time that is wrong twice a year.
#
# Install (crontab -e):
#   ZIPLINE_TRADER_CONFIG=/home/wei/Documents/zipline-yaml/zipline-trader.yaml
#   25 8 * * 1-5 /home/wei/Documents/zipline/research/run_live_momentum.sh
#
# 08:25 America/Chicago is ~65 minutes before the 09:30 New York open, and both
# zones observe DST together, so the offset holds year round. The script waits
# out the remainder.
#
# Deliberately NOT done here: flattening at the close. The strategy holds
# overnight by design -- a position exits when the counter hits -N, which may be
# tomorrow. Squaring up daily would be a different strategy with different
# results from the one that was backtested.
set -uo pipefail

RESEARCH_DIR=/home/wei/Documents/zipline/research
SYMBOLS="${SYMBOLS:-AMD GOOG UNH}"
N="${N:-8}"
LOG_DIR="${LOG_DIR:-$RESEARCH_DIR/logs}"
LOCK=/tmp/live_momentum.lock
MAX_RESTARTS="${MAX_RESTARTS:-5}"

export ZIPLINE_TRADER_CONFIG=/home/wei/Documents/zipline-yaml/zipline-trader.yaml
# shellcheck disable=SC1091
source /home/wei/anaconda3/bin/activate alpaca
cd "$RESEARCH_DIR" || exit 1
mkdir -p "$LOG_DIR"

DAY=$(date +%F)
LOG="$LOG_DIR/live_momentum_$DAY.log"

# Under cron there is no terminal and everything must go to the log. Run by
# hand, sending it all to the log makes a working script look broken: it exits
# having printed nothing, whether it did its job or fell over.
#
# Re-exec through a pipe rather than `exec > >(tee ...)`. Process substitution
# leaves tee racing the script's own exit, so the last few lines -- the ones
# that say what happened -- get lost exactly when you are watching for them.
if [ -z "${_LM_LOGGING:-}" ]; then
    export _LM_LOGGING=1
    if [ -t 1 ]; then
        echo "logging to $LOG"
        "$0" "$@" 2>&1 | tee -a "$LOG"
        exit "${PIPESTATUS[0]}"
    fi
    exec >>"$LOG" 2>&1
fi
echo "=================================================================="
echo "$(date '+%F %T %Z')  starting wrapper for [$SYMBOLS] N=$N"

# One session at a time. Two live algorithms on the same symbol do not race and
# settle -- each reads the other's fills as its own and they undo each other.
exec 9>"$LOCK"
if ! flock -n 9; then
    echo "another live_momentum session already holds $LOCK -- exiting"
    exit 0
fi

# Ask the exchange calendar, not the crontab, whether today is a session and how
# long it lasts. Half-days (the day after Thanksgiving, Christmas Eve) close at
# 13:00 and would otherwise leave the process running into nothing.
read -r IS_SESSION SECS_TO_OPEN SECS_TO_CLOSE <<<"$(python - <<'PY'
import sys
sys.path.insert(0, '/home/wei/Documents/zipline/research')
import pandas as pd
import config

cal = config.nyse()
now = pd.Timestamp.now(tz='UTC')
today = pd.Timestamp(now.tz_convert('America/New_York').date(), tz='UTC')
if not cal.is_session(today):
    print('0 0 0')
else:
    o = cal.session_open(today)
    c = cal.session_close(today)
    print('1 %d %d' % ((o - now).total_seconds(), (c - now).total_seconds()))
PY
)"

if [ "$IS_SESSION" != "1" ]; then
    echo "$(date '+%F %T')  not an NYSE session today (weekend or holiday) -- nothing to do"
    exit 0
fi
if [ "$SECS_TO_CLOSE" -le 60 ]; then
    echo "$(date '+%F %T')  today was a session but it closed $(( -SECS_TO_CLOSE / 60 )) minutes ago -- nothing to do"
    echo "                     (the next run is cron'd for 08:25; to test the wiring"
    echo "                      outside hours use: python live_smoketest.py --check)"
    exit 0
fi
if [ "$SECS_TO_OPEN" -gt 0 ]; then
    echo "$(date '+%F %T')  waiting ${SECS_TO_OPEN}s for the open"
    sleep "$SECS_TO_OPEN"
fi

echo "$(date '+%F %T')  session runs for a further ${SECS_TO_CLOSE}s"

# Restart on crash, but only while the session is still open and only a bounded
# number of times: a crash loop that reconnects every second would hammer the
# broker and fill the log rather than fix anything.
attempt=0
while :; do
    remaining=$(python -c "
import sys; sys.path.insert(0,'/home/wei/Documents/zipline/research')
import pandas as pd, config
cal = config.nyse(); now = pd.Timestamp.now(tz='UTC')
today = pd.Timestamp(now.tz_convert('America/New_York').date(), tz='UTC')
print(int(max(0, (cal.session_close(today) - now).total_seconds())))
")
    if [ "$remaining" -le 60 ]; then
        echo "$(date '+%F %T')  ${remaining}s to the close -- done for today"
        break
    fi

    attempt=$((attempt + 1))
    echo "$(date '+%F %T')  attempt $attempt, running for ${remaining}s"
    # timeout is a backstop, not the mechanism: live_momentum's own watchdog
    # polls twice a second and stops within ~5s of --max-seconds even when no
    # bars are flowing. The extra 120s only covers the case where that watchdog
    # itself fails, so a stuck process cannot run past the close into tomorrow.
    timeout -s INT $((remaining + 120)) \
        python live_momentum.py --symbols $SYMBOLS --n "$N" \
            --max-seconds "$remaining" --heartbeat 15
    rc=$?
    echo "$(date '+%F %T')  live_momentum exited rc=$rc"

    [ "$rc" -eq 0 ] && break
    if [ "$attempt" -ge "$MAX_RESTARTS" ]; then
        echo "$(date '+%F %T')  $MAX_RESTARTS failed attempts -- giving up"
        break
    fi
    sleep 30
done

echo "$(date '+%F %T')  end-of-day summary"
python eod_summary.py --symbols $SYMBOLS

echo "$(date '+%F %T')  wrapper finished"
