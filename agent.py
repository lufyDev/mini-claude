"""
The agent loop. This is the entire product; everything else is scaffolding.

    while the model asks for tools:
        stream a response -> run the tools locally -> append results -> go again

`run()` is a generator of plain-dict events. The terminal UI and the web UI are
both just renderers over the same event stream -- neither one knows anything
about the OpenAI SDK.

Uses the Responses API, not Chat Completions: the GPT-6 flagship models require
it for tool calling. `store=False` keeps the whole conversation on our side,
which is the architectural point of this project -- see README.
"""
import json
import os
import time
from pathlib import Path

import openai
from openai import OpenAI

from context import CONFIG_DIR, WORKSPACE, build_system_prompt
from tools import TOOL_SCHEMAS, run_tool
from usage import Ledger

# gpt-6-luna    $0.1/$0.5  cheap  <- default
# gpt-6.1-terra $1/$5      fast, mid
# gpt-6.1-sol   $2/$10     balanced
# gpt-6-astra   $10/$50    flagship, best at coding
MODEL = os.environ.get("OPENAI_MODEL", "gpt-6-luna")
MAX_TURNS = 20          # hard stop so a confused model can't loop forever

# Reasoning depth, and whether we get a readable summary of it to stream.
# "low" keeps the output-token bill down; raise it if the agent gets sloppy.
REASONING = {"effort": "low", "summary": "auto"}


class Agent:
    def __init__(self, session_path: Path | None = None, session_name: str = "cli",
                 workspace: Path | None = None):
        self.client = OpenAI()
        self.ledger = Ledger(MODEL, session_name)
        # Per-session on a deployment, so two visitors can't read or clobber
        # each other's files.
        self.workspace = workspace or WORKSPACE
        self.config_dir = CONFIG_DIR
        self.workspace.mkdir(parents=True, exist_ok=True)

        # Built once per session. Keeping it byte-identical across requests is
        # what lets OpenAI's automatic prefix cache hit on every later turn.
        self.system = build_system_prompt(self.workspace, self.config_dir)

        self.session_path = session_path
        # The Responses API `input` list: our entire copy of the conversation.
        self.items: list[dict] = []
        if session_path and session_path.is_file():
            self.items = json.loads(session_path.read_text())

    # ------------------------------------------------------------------ loop

    def run(self, user_text: str):
        """Yield events for one user turn, until the model stops asking for tools."""
        self.items.append({"role": "user", "content": user_text})
        usage = {}

        for turn in range(1, MAX_TURNS + 1):
            # Cheapest possible safety net: stop spending before the call, not
            # after. A runaway tool loop is the expensive failure mode here.
            if self.ledger.over_budget:
                yield {"type": "error",
                       "message": f"session budget reached ({self.ledger.summary()}). "
                                  f"Raise MINI_CLAUDE_BUDGET_USD to continue."}
                break

            try:
                calls, usage = yield from self._stream_once()
            except openai.APIStatusError as exc:
                yield {"type": "error", "message": f"API error {exc.status_code}: {exc.message}"}
                return
            except openai.APIConnectionError:
                yield {"type": "error", "message": "connection to the API failed"}
                return

            if not calls:
                break   # plain text answer -> the turn is done

            for call in calls:
                started = time.time()
                try:
                    args = json.loads(call["arguments"] or "{}")
                except json.JSONDecodeError:
                    text, is_error = f"could not parse arguments as JSON: {call['arguments'][:200]}", True
                else:
                    text, is_error = run_tool(call["name"], args, self.workspace, self.config_dir)

                yield {
                    "type": "tool_result",
                    "id": call["call_id"],
                    "name": call["name"],
                    "ok": not is_error,
                    "ms": int((time.time() - started) * 1000),
                    "result": text,
                }
                # Every function_call in the model's output needs a matching
                # function_call_output, or the next request is rejected.
                self.items.append({
                    "type": "function_call_output",
                    "call_id": call["call_id"],
                    "output": text,
                })
        else:
            yield {"type": "error", "message": f"hit the {MAX_TURNS}-turn cap"}

        self._save()
        yield {"type": "done", "usage": usage, "summary": self.ledger.summary()}

    # --------------------------------------------------------------- one call

    def _stream_once(self):
        """One API call, streamed.

        Yields events; returns (tool_calls, usage). Appends the model's output
        items to self.items -- history is append-only, we never rewrite a
        previous turn (that would blow the prefix cache, and reasoning models
        need their own reasoning items handed back verbatim).
        """
        stream = self.client.responses.create(
            model=MODEL,
            instructions=self.system,
            input=self.items,
            tools=TOOL_SCHEMAS,
            reasoning=REASONING,
            store=False,          # we keep the conversation, not OpenAI
            stream=True,
        )

        calls: dict[int, dict] = {}   # keyed by output_index, which is the only
        final = None                  # thing every event carries in common

        for event in stream:
            kind = getattr(event, "type", "")

            if kind == "response.output_text.delta":
                yield {"type": "text", "text": event.delta}

            elif kind == "response.output_item.added":
                item = event.item
                if item.type == "function_call":
                    calls[event.output_index] = {
                        "call_id": item.call_id, "name": item.name, "arguments": "",
                    }
                    yield {"type": "tool_start", "id": item.call_id, "name": item.name}
                elif item.type == "reasoning":
                    yield {"type": "thinking_start"}

            elif kind == "response.function_call_arguments.delta":
                call = calls.get(event.output_index)
                if call is not None:
                    call["arguments"] += event.delta
                yield {"type": "tool_input", "partial": event.delta}

            # Reasoning summaries, when the model emits them.
            elif kind.endswith("reasoning_summary_text.delta"):
                yield {"type": "thinking", "text": event.delta}

            elif kind == "response.completed":
                final = event.response

            elif kind in ("response.failed", "response.incomplete", "error"):
                yield {"type": "error", "message": getattr(event, "message", kind)}

        if final is None:
            yield {"type": "error", "message": "stream ended without a completed response"}
            return [], {}

        # Append everything the model produced -- text, reasoning AND function
        # calls -- before we append any results.
        # by_alias is essential: without it pydantic emits python-safe names
        # ("async_" for the reserved word "async") and the API rejects them.
        # mode="json" keeps the list straight-to-JSON for session saving.
        self.items.extend(
            item.model_dump(mode="json", by_alias=True, exclude_none=True)
            for item in final.output
        )

        ordered = [calls[i] for i in sorted(calls)]
        return ordered, self.ledger.record(self._usage(final))

    # ---------------------------------------------------------------- helpers

    def _usage(self, response) -> dict:
        u = getattr(response, "usage", None)
        if u is None:
            return {}
        details = getattr(u, "input_tokens_details", None)
        return {
            "input": getattr(u, "input_tokens", 0),
            "output": getattr(u, "output_tokens", 0),
            # OpenAI caches prefixes automatically -- there is no cache_control
            # to set. This number is how you prove the cache is being hit.
            "cached": getattr(details, "cached_tokens", 0) if details else 0,
        }

    def _save(self):
        if self.session_path:
            self.session_path.parent.mkdir(parents=True, exist_ok=True)
            self.session_path.write_text(json.dumps(self.items, indent=2, default=str))
