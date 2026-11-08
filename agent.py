"""
The agent loop. This is the entire product; everything else is scaffolding.

    while the model asks for tools:
        stream a response -> run the tools locally -> append results -> go again

`run()` is a generator of plain-dict events. The terminal UI and the web UI are
both just renderers over the same event stream -- neither one knows anything
about the Anthropic SDK.
"""
import json
import time
from pathlib import Path

import anthropic

from context import CONFIG_DIR, WORKSPACE, build_system_prompt
from tools import TOOL_SCHEMAS, run_tool

MODEL = "claude-opus-5-5"
MAX_TOKENS = 8000
MAX_TURNS = 20          # hard stop so a confused model can't loop forever
EFFORT = "medium"       # low | medium | high | xhigh | max


class Agent:
    def __init__(self, session_path: Path | None = None):
        self.client = anthropic.Anthropic()
        self.workspace = WORKSPACE
        self.config_dir = CONFIG_DIR
        self.workspace.mkdir(parents=True, exist_ok=True)

        # Built once per session. It must stay byte-identical across requests or
        # the prompt cache misses and every turn re-bills the full prefix.
        self.system = build_system_prompt(self.workspace, self.config_dir)

        self.session_path = session_path
        self.messages: list[dict] = []
        if session_path and session_path.is_file():
            self.messages = json.loads(session_path.read_text())

    # ------------------------------------------------------------------ loop

    def run(self, user_text: str):
        """Yield events for one user turn, until the model stops asking for tools."""
        self.messages.append({"role": "user", "content": user_text})

        for turn in range(1, MAX_TURNS + 1):
            try:
                response = yield from self._stream_once()
            except anthropic.APIStatusError as exc:
                yield {"type": "error", "message": f"API error {exc.status_code}: {exc.message}"}
                return
            except anthropic.APIConnectionError:
                yield {"type": "error", "message": "connection to the API failed"}
                return

            # Append the assistant turn BEFORE anything else. History is
            # append-only: editing an earlier turn invalidates the cache and,
            # on current models, the thinking blocks too.
            self.messages.append({
                "role": "assistant",
                "content": [b.model_dump(exclude_none=True) for b in response.content],
            })

            tool_uses = [b for b in response.content if b.type == "tool_use"]

            if response.stop_reason == "refusal":
                yield {"type": "error", "message": "the model declined this request"}
                break
            if response.stop_reason == "max_tokens" and tool_uses:
                yield {"type": "error", "message": "tool input was truncated; raise MAX_TOKENS"}
                break
            if not tool_uses:
                break   # plain text answer -> the turn is done

            results = []
            for block in tool_uses:
                started = time.time()
                text, is_error = run_tool(block.name, block.input, self.workspace, self.config_dir)
                yield {
                    "type": "tool_result",
                    "id": block.id,
                    "name": block.name,
                    "ok": not is_error,
                    "ms": int((time.time() - started) * 1000),
                    "result": text,
                }
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": text,
                    "is_error": is_error,
                })

            # All results from one assistant turn go back in ONE user message.
            # Splitting them teaches the model to stop calling tools in parallel.
            self.messages.append({"role": "user", "content": results})
        else:
            yield {"type": "error", "message": f"hit the {MAX_TURNS}-turn cap"}

        self._save()
        yield {"type": "done", "usage": self._usage(response)}

    # --------------------------------------------------------------- one call

    def _stream_once(self):
        """One API call, streamed. Yields events; returns the final Message."""
        with self.client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            # A list with cache_control marks a cache breakpoint. Render order is
            # tools -> system -> messages, so this one breakpoint caches both the
            # tool schemas and the system prompt on every later turn.
            system=[{
                "type": "text",
                "text": self.system,
                "cache_control": {"type": "ephemeral"},
            }],
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": EFFORT},
            tools=TOOL_SCHEMAS,
            messages=self.messages,
        ) as stream:
            for event in stream:
                if event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        yield {"type": "tool_start", "id": block.id, "name": block.name}
                    elif block.type == "thinking":
                        yield {"type": "thinking_start"}

                elif event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield {"type": "text", "text": delta.text}
                    elif delta.type == "thinking_delta":
                        yield {"type": "thinking", "text": delta.thinking}
                    elif delta.type == "input_json_delta":
                        yield {"type": "tool_input", "partial": delta.partial_json}

            return stream.get_final_message()

    # ---------------------------------------------------------------- helpers

    def _usage(self, response) -> dict:
        u = response.usage
        return {
            "input": u.input_tokens,
            "output": u.output_tokens,
            "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
        }

    def _save(self):
        if self.session_path:
            self.session_path.parent.mkdir(parents=True, exist_ok=True)
            self.session_path.write_text(json.dumps(self.messages, indent=2))
