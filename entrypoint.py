#!/usr/bin/env python3
"""
Container entrypoint: make STATE_DIR writable, then drop privileges.

A mounted volume arrives owned by root, so a container that starts as the
non-root user simply cannot write to it -- which is how you end up with a
volume attached, no error anywhere, and spend caps that silently reset on
every restart (the app logs a fallback warning, but nothing fails).

So: start as root, chown the volume, drop to `agent`, exec the real command.
The app itself never runs as root. No gosu/setpriv needed -- os.setuid is in
the standard library and this image already has Python.
"""
import grp
import os
import pwd
import sys

USER = "agent"
STATE_DIR = os.environ.get("STATE_DIR", "/data")


def prepare_state_dir(uid: int, gid: int) -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        os.chown(STATE_DIR, uid, gid)
        for root, dirs, files in os.walk(STATE_DIR):
            for name in dirs + files:
                try:
                    os.chown(os.path.join(root, name), uid, gid)
                except OSError:
                    pass
        print(f"[entrypoint] {STATE_DIR} prepared for {USER}", flush=True)
    except OSError as exc:
        # Not fatal: the app probes writability and falls back to an in-image
        # directory. Say so loudly, because the symptom otherwise is just
        # "the daily cap doesn't work" days later.
        print(f"[entrypoint] WARNING could not prepare {STATE_DIR}: {exc}. "
              f"Spend caps will reset on restart.", flush=True)


def main() -> None:
    if not sys.argv[1:]:
        sys.exit("[entrypoint] nothing to run")

    if os.geteuid() == 0:
        entry = pwd.getpwnam(USER)
        prepare_state_dir(entry.pw_uid, entry.pw_gid)
        os.setgroups([g.gr_gid for g in grp.getgrall() if USER in g.gr_mem])
        os.setgid(entry.pw_gid)
        os.setuid(entry.pw_uid)          # no way back to root after this
        print(f"[entrypoint] dropped to {USER} ({entry.pw_uid})", flush=True)

    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
