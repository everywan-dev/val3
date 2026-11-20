"""The candidate format shared by `val3 save`, the extractor and the importer."""
import re
import shlex

from val3 import Val3Error
from val3.config import parse_ref

KINDS = ("fact", "recipe", "preference", "decision", "tool")
EVIDENCE_KINDS = ("query", "command", "api", "test", "user")
TOOL_TYPES = ("skill", "mcp", "cli")
MIN_QUOTE = 8
_SHA = re.compile(r"[0-9a-f]{40}")
_INDEX = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")

FORMAT = """\
{
  "kind": "fact | recipe | preference | decision | tool",
  "title": "one line, at most 200 characters",
  "statement": "the fact, the recipe's purpose, the preference or the decision",
  "keywords": ["search terms in English and Spanish"],
  "indexes": ["existing knowledge index names (val3 index); a new one only when none fits; never the project's name"],
  "evidence": [{"kind": "query | command | api | test | user",
                "reproduction": "the query, command or call that produced the output",
                "quote": "output copied literally from the transcript, at least 8 characters"}],
  "supersedes": ["<vault>#<id> of each entry this one replaces"] (optional),
  "recipe": {"parameters": [{"name": "month", "description": "YYYY-MM"}],
             "steps": [{"action": "what to do", "tool": "<vault>#<id> (optional)",
                        "facts": ["<vault>#<id>"]}],
             "examples": [{"parameters": {"month": "2026-04"}, "result": "what it gave"}]},
  "tool": {"type": "skill | mcp | cli", "environment": "where it runs", "priority": 0}
}
Give evidence for facts, preferences and decisions: one without evidence is admitted marked
unverified, with less authority. A "user" quote must come from the user's own
messages; other quotes from tool output the call did not already contain in its command. "recipe" only for kind recipe, "tool" only for kind tool."""

_TRANSCRIPT_ITEM = """\
{"kind": "query | command | api | test | user",
  "reproduction": "the query, command or call that produced the output",
  "quote": "output copied literally from the transcript, at least 8 characters"}"""

EVIDENCE_FORMAT = "[" + _TRANSCRIPT_ITEM + """,
 {"kind": "file", "path": "path from the repository root, e.g. config/routing.yaml",
  "quote": "text of that file at HEAD, at least 8 characters"}]
The new items replace the entry's current evidence: cite every file that still backs it. A "file" quote is
checked against the committed file at HEAD; other quotes against the session transcript, as `val3 save` does."""

WITHDRAWAL_EVIDENCE_FORMAT = "[" + _TRANSCRIPT_ITEM + """]
Quotes are checked against the session transcript, as `val3 save` does. A deleted or changed file is shown by
the output of a `git show` or `git log` command.
For a shell command, "reproduction" is the exact command that was run, without secrets (passwords in
environment variables, tokens, headers): if the command held one, write it again without it. The vault records
which calls printed each quote (tool, call id and a hash of the input, never its text) and marks a
reproduction equal to the command.
A withdrawal of one entry whose evidence items all check takes effect at once. Without evidence, or for a
batch, it is a person's, in a terminal outside the agent session."""


def validate(data: object, imported: bool = False) -> dict:
    """A checked, normalised candidate, with or without evidence (ADR 0012, amendment 1). `imported` admits `file`
    evidence and an `import` block: only `val3 import` and the review of its items pass it (ADR 0003)."""
    optional = {"keywords", "indexes", "evidence", "supersedes", "recipe", "tool"} | ({"import"} if imported else set())
    obj = _object(data, "candidate", {"kind", "title", "statement"}, optional)
    kind = obj["kind"]
    if kind not in KINDS:
        raise Val3Error(f"kind must be one of: {', '.join(KINDS)}")
    title = _text(obj["title"], "title").strip()
    if len(title) > 200 or "\n" in title:
        raise Val3Error("title must be one line of at most 200 characters")
    out = {
        "kind": kind,
        "title": title,
        "statement": _text(obj["statement"], "statement").strip(),
        "keywords": [_text(k, "keywords[]").strip() for k in _list(obj.get("keywords", []), "keywords")],
        "indexes": list(dict.fromkeys(_index(name) for name in _list(obj.get("indexes", []), "indexes"))),
        "evidence": [_evidence(item, i, imported)
                     for i, item in enumerate(_list(obj.get("evidence", []), "evidence"))],
        "supersedes": _supersedes(obj.get("supersedes")),
        "recipe": None,
        "tool": None,
    }
    if imported:
        if not isinstance(obj.get("import"), dict):
            raise Val3Error('an imported candidate needs its "import" block')
        out["import"] = obj["import"]
    if (kind == "recipe") != (obj.get("recipe") is not None):
        raise Val3Error('"recipe" is required for kind recipe and allowed only there')
    if (kind == "tool") != (obj.get("tool") is not None):
        raise Val3Error('"tool" is required for kind tool and allowed only there')
    if kind == "recipe":
        out["recipe"] = _recipe(obj["recipe"])
    if kind == "tool":
        out["tool"] = _tool(obj["tool"])
    return out


def evidence_items(data: object, head: str | None = None) -> list[dict]:
    """A non-empty list of checked evidence items; `file` items only with `head`, the sha they are stored at."""
    items = _list(data, "evidence")
    if not items:
        raise Val3Error("at least one evidence item is required")
    out = []
    for i, item in enumerate(items):
        where = f"evidence[{i}]"
        if isinstance(item, dict) and item.get("kind") == "file":
            if head is None:
                raise Val3Error(f"{where}: file evidence is accepted only by `val3 confirm`")
            out.append(_file_evidence({**_object(item, where, {"kind", "path", "quote"}), "commit": head}, where))
        else:
            out.append(_evidence(item, i))
    return out


def _evidence(data: object, i: int, imported: bool = False) -> dict:
    where = f"evidence[{i}]"
    if isinstance(data, dict) and data.get("kind") == "file":
        if not imported:
            raise Val3Error(f"{where}: file evidence comes only from `val3 import`")
        return _file_evidence(data, where)
    obj = _object(data, where, {"kind", "quote"}, {"reproduction"})
    if obj["kind"] not in EVIDENCE_KINDS:
        raise Val3Error(f"{where}.kind must be one of: {', '.join(EVIDENCE_KINDS)}")
    quote = _text(obj["quote"], f"{where}.quote").replace("\r\n", "\n").strip()
    if len(quote) < MIN_QUOTE:
        raise Val3Error(f"{where}.quote must hold at least {MIN_QUOTE} characters of literal output")
    reproduction = obj.get("reproduction", "")
    if not isinstance(reproduction, str):
        raise Val3Error(f"{where}.reproduction must be a string")
    if obj["kind"] != "user" and not reproduction.strip():
        raise Val3Error(f"{where}.reproduction is required for {obj['kind']} evidence")
    return {"kind": obj["kind"], "reproduction": reproduction.strip(), "quote": quote}


def _file_evidence(data: dict, where: str) -> dict:
    """A quote the importer found in `path` at `commit`; the reproduction is derived, never supplied."""
    obj = _object(data, where, {"kind", "path", "commit", "quote"}, {"reproduction"})
    path = _text(obj["path"], f"{where}.path").strip()
    if path.startswith("/") or path.startswith("-"):
        raise Val3Error(f"{where}.path must be relative to the repository root")
    commit = obj["commit"]
    if not isinstance(commit, str) or not _SHA.fullmatch(commit):
        raise Val3Error(f"{where}.commit must be a full 40-character commit id")
    quote = _text(obj["quote"], f"{where}.quote").replace("\r\n", "\n").strip()
    if len(quote) < MIN_QUOTE:
        raise Val3Error(f"{where}.quote must hold at least {MIN_QUOTE} characters")
    reproduction = f"git show {shlex.quote(commit + ':' + path)}"
    return {"kind": "file", "path": path, "commit": commit, "reproduction": reproduction, "quote": quote}


def _recipe(data: object) -> dict:
    obj = _object(data, "recipe", {"steps"}, {"parameters", "examples"})
    parameters = []
    for i, item in enumerate(_list(obj.get("parameters", []), "recipe.parameters")):
        p = _object(item, f"recipe.parameters[{i}]", {"name"}, {"description"})
        description = p.get("description", "")
        if not isinstance(description, str):
            raise Val3Error(f"recipe.parameters[{i}].description must be a string")
        parameters.append({"name": _text(p["name"], f"recipe.parameters[{i}].name").strip(),
                           "description": description.strip()})
    names = [p["name"] for p in parameters]
    if len(set(names)) != len(names):
        raise Val3Error("recipe parameter names must be unique")
    steps = []
    for i, item in enumerate(_list(obj["steps"], "recipe.steps")):
        s = _object(item, f"recipe.steps[{i}]", {"action"}, {"tool", "facts"})
        steps.append({
            "action": _text(s["action"], f"recipe.steps[{i}].action").strip(),
            "tool": _ref(s["tool"], f"recipe.steps[{i}].tool") if s.get("tool") is not None else None,
            "facts": [_ref(ref, f"recipe.steps[{i}].facts[]")
                      for ref in _list(s.get("facts", []), f"recipe.steps[{i}].facts")],
        })
    if not steps:
        raise Val3Error("a recipe needs at least one step")
    examples = []
    for i, item in enumerate(_list(obj.get("examples", []), "recipe.examples")):
        e = _object(item, f"recipe.examples[{i}]", {"parameters", "result"})
        values = e["parameters"]
        if not isinstance(values, dict) or set(values) - set(names):
            raise Val3Error(f"recipe.examples[{i}].parameters must be an object of declared parameters")
        examples.append({"parameters": values, "result": _text(e["result"], f"recipe.examples[{i}].result")})
    return {"parameters": parameters, "steps": steps, "examples": examples}


def _tool(data: object) -> dict:
    obj = _object(data, "tool", {"type"}, {"environment", "priority"})
    if obj["type"] not in TOOL_TYPES:
        raise Val3Error(f"tool.type must be one of: {', '.join(TOOL_TYPES)}")
    environment = obj.get("environment", "")
    priority = obj.get("priority", 0)
    if not isinstance(environment, str):
        raise Val3Error("tool.environment must be a string")
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise Val3Error("tool.priority must be an integer")
    return {"type": obj["type"], "environment": environment.strip(), "priority": priority}


def _object(data: object, where: str, required: set, optional: set = frozenset()) -> dict:
    if not isinstance(data, dict):
        raise Val3Error(f"{where} must be a JSON object")
    missing = required - set(data)
    unknown = set(data) - required - set(optional)
    if missing:
        raise Val3Error(f"{where} is missing: {', '.join(sorted(missing))}")
    if unknown:
        raise Val3Error(f"{where} has unknown keys: {', '.join(sorted(unknown))}")
    return data


def _list(data: object, where: str) -> list:
    if not isinstance(data, list):
        raise Val3Error(f"{where} must be a list")
    return data


def _text(data: object, where: str) -> str:
    if not isinstance(data, str) or not data.strip():
        raise Val3Error(f"{where} must be a non-empty string")
    return data


def _index(data: object) -> str:
    """Index names are normalised (lowercase, other characters to '-'), not rejected."""
    name = re.sub(r"[^a-z0-9-]+", "-", data.strip().lower()).strip("-")[:63] if isinstance(data, str) else ""
    if not _INDEX.fullmatch(name):
        raise Val3Error(f"invalid index name {data!r}: use lowercase letters, digits and '-'")
    return name


def _supersedes(data: object) -> list[str]:
    """Absent or null: none; one ref as a string (payloads queued before the list); a list, deduplicated in order."""
    if data is None:
        return []
    if isinstance(data, str):
        return [_ref(data, "supersedes")]
    return list(dict.fromkeys(_ref(ref, "supersedes[]") for ref in _list(data, "supersedes")))


def _ref(data: object, where: str) -> str:
    vault, entry_id = parse_ref(_text(data, where))
    return f"{vault}#{entry_id}"
