"""
Terminal REPL. One of two renderers over Agent.run()'s event stream.

    python main.py                  interactive
    python main.py "build me a fizzbuzz and run it"      one-shot
"""
import sys
from datetime import datetime

from agent import Agent
from context import CONFIG_DIR

DIM, BLUE, GREEN, RED, YELLOW, RESET = (
    "\033[2m", "\033[34m", "\033[32m", "\033[31m", "\033[33m", "\033[0m",
)


def render(agent, text):
    in_text = False
    for ev in agent.run(text):
        kind = ev["type"]

        if kind == "thinking_start":
            print(f"\n{DIM}[thinking]{RESET} ", end="", flush=True)
        elif kind == "thinking":
            print(f"{DIM}{ev['text']}{RESET}", end="", flush=True)

        elif kind == "text":
            if not in_text:
                print()
                in_text = True
            print(ev["text"], end="", flush=True)

        elif kind == "tool_start":
            in_text = False
            print(f"\n{BLUE}  {ev['name']}{RESET}", end="", flush=True)
        elif kind == "tool_input":
            print(f"{DIM}{ev['partial']}{RESET}", end="", flush=True)
        elif kind == "tool_result":
            colour = GREEN if ev["ok"] else RED
            head = ev["result"].splitlines()[0][:100] if ev["result"] else ""
            print(f"\n{colour}  -> {head}{RESET} {DIM}({ev['ms']}ms){RESET}")

        elif kind == "error":
            print(f"\n{RED}! {ev['message']}{RESET}")
        elif kind == "done":
            u = ev["usage"]
            print(
                f"\n{DIM}[in {u['input']} / out {u['output']} / "
                f"cache read {u['cache_read']} write {u['cache_write']}]{RESET}\n"
            )


def main():
    session = CONFIG_DIR / "sessions" / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    agent = Agent(session_path=session)

    if len(sys.argv) > 1:
        render(agent, " ".join(sys.argv[1:]))
        return

    print(f"{YELLOW}mini-claude{RESET} {DIM}-- workspace: {agent.workspace}  (ctrl-c to quit){RESET}")
    while True:
        try:
            text = input(f"\n{YELLOW}> {RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{DIM}session saved to {session}{RESET}")
            return
        if text:
            render(agent, text)


if __name__ == "__main__":
    main()
