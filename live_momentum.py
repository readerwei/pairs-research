"""Backwards-compatible shim: naive momentum through the generic runner.

    python live_momentum.py --check
    python live_momentum.py --max-seconds 300

Equivalent to `live_runner.py --strategy naive_momentum ...`. Kept so existing
commands and notes keep working; new work should use live_runner.py directly.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import live_runner

if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--strategy' not in argv:
        argv = ['--strategy', 'naive_momentum'] + argv
    if not any(a in argv for a in ('--check', '--once', '--session')):
        argv.append('--once')
    argv = [a.replace('--n', '--param n=') if a.startswith('--n=') else a
            for a in argv]
    sys.argv = [sys.argv[0]] + argv
    sys.exit(live_runner.main() or 0)
