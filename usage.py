"""
Token and cost accounting.

Every API call is recorded to .miniclaude/usage.jsonl, priced, and totalled.
A session that crosses BUDGET_USD stops making calls rather than silently
running up a bill.

    python usage.py          what have I spent, all time and per model
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from context import CONFIG_DIR

LEDGER = CONFIG_DIR / "usage.jsonl"

# USD per 1,000,000 tokens: (input, output). From OpenAI's models page.
PRICES = {
    "gpt-6-astra":   (10.00, 50.00),
    "gpt-6.1-sol":   (2.00, 10.00),
    "gpt-6.1-terra": (1.00,  5.00),
    "gpt-6-luna":    (0.10,  0.50),
}

# Cached input tokens bill at 10% of the normal input rate.
CACHED_RATE = 0.10

# Hard stop per session. Override with MINI_CLAUDE_BUDGET_USD.
BUDGET_USD = float(os.environ.get("MINI_CLAUDE_BUDGET_USD", "0.50"))


def price(model: str, inp: int, cached: int, out: int) -> float | None:
    """Cost in USD, or None if we don't have a price for this model."""
    if model not in PRICES:
        return None
    in_rate, out_rate = PRICES[model]
    fresh = max(inp - cached, 0)
    return (
        fresh * in_rate / 1_000_000
        + cached * in_rate * CACHED_RATE / 1_000_000
        + out * out_rate / 1_000_000
    )


class Ledger:
    """One per session. Accumulates in memory, appends to disk as it goes."""

    def __init__(self, model: str, session: str = "cli"):
        self.model = model
        self.session = session
        self.calls = 0
        self.input = self.cached = self.output = 0
        self.usd = 0.0
        self.priced = True

    def record(self, usage: dict) -> dict:
        inp = usage.get("input", 0) or 0
        cached = usage.get("cached", 0) or 0
        out = usage.get("output", 0) or 0

        cost = price(self.model, inp, cached, out)
        if cost is None:
            self.priced = False
            cost = 0.0

        self.calls += 1
        self.input += inp
        self.cached += cached
        self.output += out
        self.usd += cost

        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "session": self.session,
            "model": self.model,
            "input": inp, "cached": cached, "output": out,
            "usd": round(cost, 6),
        }
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a") as fh:
            fh.write(json.dumps(row) + "\n")

        return {
            **usage,
            "usd": cost,
            "session_usd": self.usd,
            "budget_usd": BUDGET_USD,
            "priced": self.priced,
        }

    @property
    def over_budget(self) -> bool:
        return self.usd >= BUDGET_USD

    def summary(self) -> str:
        hit = f"{100 * self.cached / self.input:.0f}%" if self.input else "0%"
        money = f"${self.usd:.4f}" if self.priced else f"${self.usd:.4f}+ (unpriced model)"
        return (
            f"{self.calls} calls · in {self.input:,} (cache {hit}) · "
            f"out {self.output:,} · {money} of ${BUDGET_USD:.2f}"
        )


def report() -> None:
    if not LEDGER.is_file():
        print("no usage recorded yet")
        return
    rows = [json.loads(l) for l in LEDGER.read_text().splitlines() if l.strip()]
    per_model: dict[str, dict] = {}
    for r in rows:
        acc = per_model.setdefault(r["model"], {"calls": 0, "input": 0, "cached": 0, "output": 0, "usd": 0.0})
        for k in ("input", "cached", "output", "usd"):
            acc[k] += r[k]
        acc["calls"] += 1

    print(f"{'model':<16} {'calls':>6} {'input':>12} {'cached':>10} {'output':>10} {'usd':>10}")
    print("-" * 68)
    for model, a in sorted(per_model.items()):
        print(f"{model:<16} {a['calls']:>6} {a['input']:>12,} {a['cached']:>10,} {a['output']:>10,} {a['usd']:>10.4f}")
    print("-" * 68)
    total = sum(a["usd"] for a in per_model.values())
    calls = sum(a["calls"] for a in per_model.values())
    print(f"{'TOTAL':<16} {calls:>6} {'':>12} {'':>10} {'':>10} {total:>10.4f}")
    print(f"\n{len(rows)} calls across {len({r['session'] for r in rows})} sessions")


if __name__ == "__main__":
    report()
