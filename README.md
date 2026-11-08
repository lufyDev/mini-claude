# mini-claude

A ~500-line coding agent: the parts of Claude Code that matter, with the parts
that don't deliberately left out. Terminal REPL and a web UI that streams the
agent's thinking, tool calls and results live.

## Run it

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
export OPENAI_API_KEY=sk-...

.venv/bin/python models.py                       # which models can my key see?
.venv/bin/python main.py                         # terminal
.venv/bin/python main.py "write fizzbuzz"        # one-shot
.venv/bin/python -m uvicorn server:app --reload  # web UI on :8000
```

Model via `OPENAI_MODEL` (default `gpt-6.1-sol`; `gpt-6-astra` is stronger at
code and 5x the price; `gpt-6-luna` is the cheap one).

## How it works

```
main.py / server.py     two renderers over one event stream
        |
    agent.py            the loop: stream -> run tools -> append -> repeat
       /  \
context.py  tools.py    what the model knows     what the model can do
        |
 .miniclaude/           CONTEXT.md, skills/, sessions/
```

**The loop is the product.** `agent.py` sends the conversation, and if the reply
contains `tool_use` blocks it runs them locally, appends the results, and sends
again. It stops when the model answers in plain text.

**The model is stateless.** Nothing persists server-side. Every turn re-sends the
whole conversation, so all "memory" is this harness rebuilding a text blob from
disk. `context.py` is that rebuild.

**The model has no hands.** It can't read a file; it emits
`{"type":"tool_use","name":"read_file",...}` and `tools.py` decides what actually
happens. Model-supplied paths are untrusted output — `_safe_path` refuses
anything outside `workspace/`, and `run_bash` has a blocklist and a 30s timeout.

**Skills load lazily.** Only each skill's `name` + `description` go into the
system prompt. The body arrives when the model calls `load_skill`. 50 skills cost
50 lines of context instead of 50 documents.

**History is append-only, and we own it.** This uses the **Responses API** (the
GPT-6 flagship models require it for tool calling) with `store=False`, so OpenAI
keeps nothing — `self.items` is the only copy of the conversation. The obvious
alternative, `previous_response_id`, would let OpenAI hold the thread and hide
the exact thing this project exists to show.

Two consequences. OpenAI caches long prefixes automatically — there's no
`cache_control` to set — so never rewriting an earlier turn is what makes the
cache hit; the `cached` number in the usage line is the proof. And reasoning
models need their own `reasoning` items handed back verbatim alongside the tool
outputs, which falls out of appending all of `response.output` unmodified.

**One event stream, two UIs.** `Agent.run()` yields plain dicts
(`text`, `thinking`, `tool_start`, `tool_input`, `tool_result`, `done`). The
terminal prints them with ANSI colours; `server.py` reserialises the same dicts
as SSE frames. Neither renderer imports the Anthropic SDK.

## Deliberately not built

Permission prompts, context compaction, subagents, hooks, MCP, multi-file edits.
Each is a known layer of the real thing, left out to keep the loop readable.

## Swapping the model provider

`main.py`, `server.py` and `static/index.html` contain zero provider code — this
was ported from the Anthropic SDK to OpenAI by rewriting `agent.py` and the tool
schemas in `tools.py`, and nothing else. That's the payoff of the event-dict
boundary.

The schemas differ more than you'd expect: OpenAI's `strict: true` demands
`additionalProperties: false` *and* every property listed in `required`, so
there are no optional tool arguments here.

## Tools

| Tool | What it does |
|---|---|
| `list_files` | List a directory in the workspace |
| `read_file` | Read a text file, line-numbered |
| `write_file` | Create/overwrite a file |
| `run_bash` | Shell command in the workspace, 30s timeout, blocklisted patterns |
| `load_skill` | Pull one skill's full instructions into context |

## Config

| Path | Purpose |
|---|---|
| `.miniclaude/CONTEXT.md` | Project rules, pasted into the system prompt verbatim (the `CLAUDE.md` equivalent) |
| `.miniclaude/skills/<name>/SKILL.md` | A skill: YAML frontmatter (`name`, `description`) + instructions |
| `.miniclaude/sessions/*.json` | Saved transcripts |
| `workspace/` | The only directory the agent can touch |

Model and limits are constants at the top of `agent.py`:
`MODEL`, `MAX_TOKENS`, `MAX_TURNS`, `EFFORT`.
