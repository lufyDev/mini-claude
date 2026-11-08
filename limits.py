"""
Deployment policy. Everything here is off by default and switches on with
DEMO_MODE=1, so local use stays unrestricted.

The threats for a public demo, in order of how much they'd actually cost you:
  1. someone reads OPENAI_API_KEY out of the shell  -> env scrub (always on)
  2. someone burns your credits                     -> daily cap + per-session cap
  3. someone uses your box to attack something      -> command allowlist
  4. someone sees/overwrites another visitor's work -> per-session workspaces
"""
import json
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

DEMO_MODE = os.environ.get("DEMO_MODE") == "1"

# Spend ceiling across every visitor, per UTC day. The one that actually
# protects your wallet -- a per-session cap alone is defeated by reloading.
DAILY_USD_CAP = float(os.environ.get("DAILY_USD_CAP", "1.00"))

# Turns one visitor gets before they're asked to run it locally.
SESSION_MSG_CAP = int(os.environ.get("SESSION_MSG_CAP", "10"))

# Crude per-IP throttle.
RATE_PER_MIN = int(os.environ.get("RATE_PER_MIN", "6"))

# Caps on what the agent may leave behind.
MAX_WRITE_BYTES = 64_000
MAX_WORKSPACE_FILES = 40

# Ceilings on a single tool subprocess, applied by the shell itself. The host's
# container limit protects the host; this protects the container, so one
# `python3 -c "x = [0] * 10**9"` from a visitor can't take the service down.
#   -t  CPU seconds (under the 30s wall timeout, so this trips first)
#   -v  virtual memory, KB
# Linux honours both. macOS has no -v, hence the suppressed errors -- the
# command still runs, just unbounded, which is fine for local use.
SUBPROCESS_CPU_SECONDS = 25
SUBPROCESS_MEMORY_KB = 512_000
ULIMIT_PREFIX = (
    f"ulimit -t {SUBPROCESS_CPU_SECONDS} 2>/dev/null; "
    f"ulimit -v {SUBPROCESS_MEMORY_KB} 2>/dev/null; "
)

# In demo mode a command's first word must be in here. This is the line that
# stops `curl`, `pip`, `nc`, `ssh` and friends -- an allowlist, because a
# blocklist of dangerous commands is a list you will always be missing one of.
ALLOWED_COMMANDS = {
    "python3", "python", "ls", "cat", "head", "tail", "grep", "wc",
    "echo", "pwd", "find", "sort", "uniq", "diff", "true",
}

# Shell operators we split on before checking each segment's first word.
_SEPARATORS = ("&&", "||", ";", "|", "\n")


def command_allowed(command: str) -> tuple[bool, str]:
    """(ok, reason). Only enforced in demo mode."""
    if not DEMO_MODE:
        return True, ""
    if any(ch in command for ch in "`$><"):
        return False, "demo mode: no substitution or redirection"
    rest = command
    for sep in _SEPARATORS:
        rest = rest.replace(sep, "\n")
    for segment in rest.split("\n"):
        segment = segment.strip()
        if not segment:
            continue
        first = segment.split()[0]
        if first not in ALLOWED_COMMANDS:
            return False, (
                f"demo mode: '{first}' is not allowed. "
                f"Available: {', '.join(sorted(ALLOWED_COMMANDS))}"
            )
    return True, ""


def clean_env() -> dict:
    """The environment a tool subprocess gets.

    Always applied, demo or not: the agent has a shell, and `env` would
    otherwise print your API key straight into the transcript.
    """
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR")
    return {k: v for k, v in os.environ.items() if k in keep}


def spent_today(ledger_path) -> float:
    """Sum of usage.jsonl rows dated today (UTC)."""
    if not ledger_path.is_file():
        return 0.0
    today = datetime.now(timezone.utc).date().isoformat()
    total = 0.0
    try:
        for line in ledger_path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("ts", "").startswith(today):
                total += row.get("usd", 0.0)
    except (OSError, json.JSONDecodeError):
        return 0.0
    return total


class RateLimiter:
    """Fixed window per key. Good enough; not a security boundary on its own."""

    def __init__(self, per_minute: int = RATE_PER_MIN):
        self.per_minute = per_minute
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        if not DEMO_MODE:
            return True
        now = time.time()
        window = self.hits[key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.per_minute:
            return False
        window.append(now)
        return True
