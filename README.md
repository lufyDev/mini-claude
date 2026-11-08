# mini-claude

A ~500-line coding agent: the parts of Claude Code that matter, with the parts
that don't deliberately left out. Terminal REPL and a web UI that streams the
agent's thinking, tool calls and results live.

## Run it

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # then put your OPENAI_API_KEY in it

.venv/bin/python models.py                       # which models can my key see?
.venv/bin/python main.py                         # terminal
.venv/bin/python main.py "write fizzbuzz"        # one-shot
.venv/bin/python -m uvicorn server:app --reload  # web UI on :8000
.venv/bin/python usage.py                        # what have I spent
```

## Cost

Default model is `gpt-6-luna` ($0.10/$0.50 per MTok), the cheapest GPT-6. A
real task — write a script, run it, fix it, report — is **4-5 API calls and
about $0.0006**. Set `OPENAI_MODEL` to spend more on harder work:
`gpt-6.1-terra` ($1/$5), `gpt-6.1-sol` ($2/$10), `gpt-6-astra` ($10/$50, best
at code).

Every call is priced and appended to `.miniclaude/usage.jsonl`. `usage.py`
totals it per model. A session stops making calls once it crosses
`MINI_CLAUDE_BUDGET_USD` (default `$0.50`) — checked *before* each call, since a
runaway tool loop is the expensive failure mode. `reasoning.effort` is `"low"`
in `agent.py`; raise it if the agent gets sloppy.

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

> Measured: the first call of a session is 0% cached and the prefix is ~800
> tokens, below OpenAI's 1024-token caching floor. Once the conversation grows
> past it the cache kicks in hard — **35% by turn two, 63-71% by turn three.**
> So the floor is real but you clear it almost immediately, and the payoff is
> large. Worth knowing that a *bigger* system prompt is what gets you over the
> floor sooner, which is the opposite of the usual "keep prompts small"
> instinct.

**You can watch the workspace.** The right-hand pane lists everything under
`workspace/` and refreshes itself whenever a tool writes; click a file to read
it. `/api/file` reuses the exact same `safe_path` guard the tools do, so the
viewer can't read outside the workspace either.

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

Watching it self-correct is the best part of the demo: `run_bash("python x.py")`
returns exit 127, so it tries `python3` and moves on. Nothing in the harness
handles that — the tool result is just true information, and the loop lets the
model act on it.

## Config

| Path | Purpose |
|---|---|
| `.miniclaude/CONTEXT.md` | Project rules, pasted into the system prompt verbatim (the `CLAUDE.md` equivalent) |
| `.miniclaude/skills/<name>/SKILL.md` | A skill: YAML frontmatter (`name`, `description`) + instructions |
| `.miniclaude/sessions/*.json` | Saved transcripts |
| `.miniclaude/usage.jsonl` | One line per API call: tokens and cost |
| `.env` | `OPENAI_API_KEY`, `OPENAI_MODEL` (gitignored) |
| `workspace/` | The only directory the agent can touch |

Knobs: `MODEL`, `MAX_TURNS`, `REASONING` at the top of `agent.py`;
`PRICES`, `BUDGET_USD` in `usage.py`.
