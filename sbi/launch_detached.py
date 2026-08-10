#!/usr/bin/env python3
"""Launch an arm FULLY DETACHED from this session's process group.

Why this exists: `nohup ... &` from the agent shell leaves the child in the shell's
process group, so when the harness cancels/kills a foreground command it can take the
background arms down with it. That happened once here -- the GT arm (94/300) and the
refined arm (65/300) were both killed with no error in their logs. macOS has no
`setsid`, so detachment is done with os.setsid() in a forked child.

Idempotent: run_arm.py skips terminal records, so relaunching costs nothing and a kill
costs zero completed work.

Usage: python3 launch_detached.py <arm> [--par N]
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    arm = sys.argv[1]
    extra = sys.argv[2:]
    log = os.path.join(ROOT, "logs", f"{arm}_full.log")
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)

    pid = os.fork()
    if pid > 0:
        # parent: report and exit immediately so the harness never waits on us
        print(f"launched {arm} detached (child pid {pid}) -> {log}")
        os._exit(0)

    os.setsid()                      # new session + process group: immune to the
    os.chdir(ROOT)                   # shell's group-wide signals
    # -u / PYTHONUNBUFFERED: stdout to a regular FILE is block-buffered (~8KB), so the
    # progress log appeared frozen for many minutes even though the arm was healthy and
    # writing records. The per-statement JSON records are the real data (each carries its
    # own usage/meta/outcome), but an unreadable progress log makes a live run look dead.
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    with open(log, "a", buffering=1) as f, open(os.devnull) as devnull:
        p = subprocess.Popen(
            [sys.executable, "-u", os.path.join(ROOT, "run_arm.py"), arm] + extra,
            stdout=f, stderr=subprocess.STDOUT, stdin=devnull,
            start_new_session=True, cwd=ROOT, env=env)
        f.write(f"\n--- detached launch: {arm} pid={p.pid} ---\n")
        f.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
