"""Backwards-compatible shim: the ping-pong plumbing test through the runner.

    python live_smoketest.py --check
    python live_smoketest.py --max-seconds 300

Equivalent to `live_runner.py --strategy pingpong ...`.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import live_runner

if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--strategy' not in argv:
        argv = ['--strategy', 'pingpong'] + argv
    if not any(a in argv for a in ('--check', '--once', '--session')):
        argv.append('--once')
    sys.argv = [sys.argv[0]] + argv
    sys.exit(live_runner.main() or 0)
