"""
Tool layer.

Two halves per tool:
  - a JSON schema the model sees   (TOOL_SCHEMAS)
  - a local python function it never sees (HANDLERS)

The model can only ever *ask* for a tool. This file decides what actually runs.
Every path is confined to WORKSPACE -- the model's `path` argument is untrusted
output, not validated input.
"""
import json
import subprocess
from pathlib import Path

from limits import (MAX_WORKSPACE_FILES, MAX_WRITE_BYTES, ULIMIT_PREFIX,
                    clean_env, command_allowed)

# ---------------------------------------------------------------- schemas
#
# Responses API shape: flat (no nested "function" object), and `strict: True`
# requires additionalProperties:false plus EVERY property listed in `required`.
# So there are no optional arguments here by design.

def _tool(name, description, properties):
    return {
        "type": "function",
        "name": name,
        "description": description,
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


TOOL_SCHEMAS = [
    _tool(
        "list_files",
        "List files and directories under a path in the workspace. "
        "Pass '.' for the workspace root. Start here when you don't know the layout.",
        {"path": {"type": "string", "description": "Relative path, or '.' for the root"}},
    ),
    _tool(
        "read_file",
        "Read a UTF-8 text file from the workspace. Returns the whole file with line numbers.",
        {"path": {"type": "string", "description": "Path relative to the workspace root"}},
    ),
    _tool(
        "write_file",
        "Create or overwrite a file in the workspace with the given content. "
        "Read the file first if you are modifying an existing one.",
        {
            "path": {"type": "string", "description": "Path relative to the workspace root"},
            "content": {"type": "string", "description": "The complete new contents of the file"},
        },
    ),
    _tool(
        "run_bash",
        "Run a shell command inside the workspace directory and return stdout+stderr. "
        "Use for running scripts, tests and greps. 30s timeout.",
        {"command": {"type": "string", "description": "The shell command to run"}},
    ),
    _tool(
        "load_skill",
        "Load the full instructions for one of the skills listed in your system prompt. "
        "Only the name and description are preloaded -- call this to get the actual steps.",
        {"name": {"type": "string", "description": "The skill's name, exactly as listed"}},
    ),
]


# ---------------------------------------------------------------- guards

MAX_OUTPUT_CHARS = 20_000

# Kept for local use, where limits.ALLOWED_COMMANDS is not enforced. A blocklist
# is a list you are always missing one entry of -- the allowlist in limits.py is
# the real control, this is just a seatbelt.
BLOCKED = ("rm -rf /", "mkfs", "shutdown", "reboot", ":(){", "dd if=")


def _safe_path(workspace: Path, raw: str) -> Path:
    """Resolve a model-supplied path and refuse anything outside the workspace."""
    target = (workspace / (raw or ".")).resolve()
    if not target.is_relative_to(workspace.resolve()):
        raise ValueError(f"path escapes the workspace: {raw}")
    return target


safe_path = _safe_path   # re-exported: server.py uses the same guard


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n... [clipped, {len(text) - MAX_OUTPUT_CHARS} more chars]"


# ---------------------------------------------------------------- handlers

def _list_files(workspace, config_dir, path="."):
    target = _safe_path(workspace, path)
    if not target.exists():
        return f"no such path: {path}"
    if target.is_file():
        return f"{path} (file, {target.stat().st_size} bytes)"
    rows = []
    for child in sorted(target.iterdir()):
        if child.name.startswith("."):
            continue
        rows.append(f"{child.name}/" if child.is_dir() else child.name)
    return "\n".join(rows) or "(empty directory)"


def _read_file(workspace, config_dir, path):
    target = _safe_path(workspace, path)
    if not target.is_file():
        return f"no such file: {path}"
    try:
        body = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"{path} is not a UTF-8 text file"
    numbered = "\n".join(f"{i:>4}  {line}" for i, line in enumerate(body.splitlines(), 1))
    return _clip(numbered) or "(empty file)"


def _write_file(workspace, config_dir, path, content):
    target = _safe_path(workspace, path)
    if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
        return f"refused: file would be larger than {MAX_WRITE_BYTES} bytes"
    existing = sum(1 for p in workspace.rglob("*") if p.is_file())
    if existing >= MAX_WORKSPACE_FILES and not target.exists():
        return f"refused: workspace already holds {MAX_WORKSPACE_FILES} files"
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    target.write_text(content, encoding="utf-8")
    verb = "overwrote" if existed else "created"
    return f"{verb} {path} ({len(content)} chars, {len(content.splitlines())} lines)"


def _run_bash(workspace, config_dir, command):
    low = command.lower()
    for bad in BLOCKED:
        if bad in low:
            return f"refused: command matches blocked pattern {bad!r}"
    ok, reason = command_allowed(command)
    if not ok:
        return f"refused: {reason}"
    try:
        proc = subprocess.run(
            # The ulimits are prepended after validation, so the model's own
            # command is what got checked against the allowlist.
            ULIMIT_PREFIX + command,
            shell=True, cwd=workspace, capture_output=True,
            text=True, timeout=30,
            # Never hand the API key to a shell the model controls: `env`
            # would print it straight into the transcript.
            env=clean_env(),
        )
    except subprocess.TimeoutExpired:
        return "command timed out after 30s"
    out = (proc.stdout or "") + (proc.stderr or "")
    return _clip(f"exit code: {proc.returncode}\n{out.strip() or '(no output)'}")


def _load_skill(workspace, config_dir, name):
    skill_file = config_dir / "skills" / name / "SKILL.md"
    if not skill_file.is_file():
        return f"no skill named {name!r}. Check the list in your system prompt."
    return _clip(skill_file.read_text(encoding="utf-8"))


HANDLERS = {
    "list_files": _list_files,
    "read_file": _read_file,
    "write_file": _write_file,
    "run_bash": _run_bash,
    "load_skill": _load_skill,
}


def run_tool(name: str, args: dict, workspace: Path, config_dir: Path) -> tuple[str, bool]:
    """Execute a tool. Returns (result_text, is_error).

    Never raises: a crashed tool is information the model can act on, so it goes
    back as a readable sentence rather than killing the loop.
    """
    handler = HANDLERS.get(name)
    if handler is None:
        return f"unknown tool: {name}", True
    if not isinstance(args, dict):
        return json.dumps({"INVALID_JSON": str(args)}), True
    try:
        return handler(workspace, config_dir, **args), False
    except TypeError as exc:
        return f"bad arguments for {name}: {exc}", True
    except Exception as exc:
        return f"{name} failed: {type(exc).__name__}: {exc}", True
