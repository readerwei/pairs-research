#!/bin/bash
# Compatibility shim. This path is wired into crontab; the refactor moved the
# implementation to run_live.sh and deleting this file silently stopped the
# 08:25 job -- cron ran it, found nothing, and the strategy did not trade.
#
# Equivalent to: run_live.sh --strategy naive_momentum
exec /home/wei/Documents/zipline/research/run_live.sh --strategy naive_momentum "$@"
