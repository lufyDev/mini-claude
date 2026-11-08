"""
Context assembly -- the equivalent of Claude Code's ~/.claude + CLAUDE.md layer.

The model is stateless, so the whole "who am I, where am I, what can I do"
briefing is rebuilt from disk on every single request. Nothing here is magic:
it all ends up as one string in the `system` field.

Skills use progressive disclosure: only `name` + `description` go into the
system prompt. The body is loaded on demand by the `load_skill` tool.
"""
import datetime
import platform
from pathlib import Path

IDENTITY = """You are mini-claude, a small coding agent that works inside a single workspace directory.

How you operate:
- You cannot see or touch anything directly. To learn or change anything, call a tool.
- Explore before you edit: list_files and read_file before write_file.
- After changing code, verify it with run_bash (run the script, run the tests) instead of assuming.
- When a skill in the list below matches the task, call load_skill to get its full instructions before starting.
- Be concise. Explain what you did, not what you are about to do."""


ROOT = Path(__file__).parent.resolve()
CONFIG_DIR = ROOT / ".miniclaude"
WORKSPACE = ROOT / "workspace"


def discover_skills(cfg: Path) -> list[dict]:
    """Read name + description out of each .miniclaude/skills/*/SKILL.md frontmatter."""
    skills = []
    skills_dir = cfg / "skills"
    if not skills_dir.is_dir():
        return skills
    for skill_file in sorted(skills_dir.glob("*/SKILL.md")):
        meta = _frontmatter(skill_file.read_text(encoding="utf-8"))
        skills.append({
            "name": meta.get("name", skill_file.parent.name),
            "description": meta.get("description", "(no description)"),
        })
    return skills


def _frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    _, _, rest = text.partition("---\n")
    block, _, _ = rest.partition("\n---")
    out = {}
    for line in block.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            out[key.strip()] = value.strip()
    return out


def build_system_prompt(workspace: Path, cfg: Path) -> str:
    parts = [IDENTITY]

    parts.append(
        "\n# Environment\n"
        f"- Workspace root: {workspace}\n"
        f"- Platform: {platform.system()}\n"
        f"- Today: {datetime.date.today().isoformat()}\n"
        "- All tool paths are relative to the workspace root. You cannot read or write outside it."
    )

    # Project rules -- this is the CLAUDE.md equivalent, pasted in verbatim.
    rules = cfg / "CONTEXT.md"
    if rules.is_file():
        parts.append("\n# Project rules (from .miniclaude/CONTEXT.md)\n" + rules.read_text(encoding="utf-8").strip())

    # Skills: labels only. Bodies load on demand -- this is the whole trick.
    skills = discover_skills(cfg)
    if skills:
        listing = "\n".join(f"- **{s['name']}**: {s['description']}" for s in skills)
        parts.append(
            "\n# Available skills\n"
            "Only the names and descriptions are loaded. Call `load_skill` to read the full instructions "
            "for one before doing work it covers.\n" + listing
        )

    return "\n".join(parts)
