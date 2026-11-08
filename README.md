# mini-claude

A ~500-line coding agent: the parts of Claude Code that matter, with the parts
that don't deliberately left out. Terminal REPL and a web UI that streams the
agent's thinking, tool calls and results live.

## Run it

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...

.venv/bin/python main.py                       # terminal
.venv/bin/python main.py "write fizzbuzz"      # one-shot
.venv/bin/python -m uvicorn server:app --reload  # web UI on :8000
```

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

**History is append-only.** The system prompt is built once per session and
carries a `cache_control` breakpoint, so from turn two the whole prefix (tool
schemas + system prompt) is a cache read. Editing an earlier turn would throw
that away — watch `cache read` in the usage line to see it working.

**One event stream, two UIs.** `Agent.run()` yields plain dicts
(`text`, `thinking`, `tool_start`, `tool_input`, `tool_result`, `done`). The
terminal prints them with ANSI colours; `server.py` reserialises the same dicts
as SSE frames. Neither renderer imports the Anthropic SDK.

## Deliberately not built

Permission prompts, context compaction, subagents, hooks, MCP, multi-file edits.
Each is a known layer of the real thing, left out to keep the loop readable.

## Tools

| Tool | What it does |
|---|---|
| `list_files` | List a directory in the workspace |
| `read_file` | Read a text file, line-numbered |
| `write_file` | Create/overwrite a file (`eager_input_streaming` so big bodies stream) |
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
