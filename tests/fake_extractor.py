"""Stand-in for `claude -p` in tests: records each call and answers from prepared files.

FAKE_EXTRACTOR_DIR holds response-<n>.json for call n (default: no candidates) and an optional
`exit` file with the exit code to return; edit-<n>.json ({"path", "text"}) appends to a file
during call n; command-<n>.json ({"argv", "cwd"}) runs before call n answers, without VAL3_EXTRACTING.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

directory = Path(os.environ["FAKE_EXTRACTOR_DIR"])
call = len(list(directory.glob("call-*.json")))
(directory / f"call-{call}.json").write_text(json.dumps(
    {"argv": sys.argv[1:], "stdin": sys.stdin.read(), "extracting": os.environ.get("VAL3_EXTRACTING")}))
edit = directory / f"edit-{call}.json"
if edit.exists():  # simulate the session editing a file while the extractor runs
    spec = json.loads(edit.read_text())
    with open(spec["path"], "a") as handle:
        handle.write(spec["text"])
command = directory / f"command-{call}.json"
if command.exists():  # simulate the session acting while the extractor runs
    spec = json.loads(command.read_text())
    subprocess.run(spec["argv"], cwd=spec["cwd"], capture_output=True,
                   env={k: v for k, v in os.environ.items() if k != "VAL3_EXTRACTING"})
exit_file = directory / "exit"
code = int(exit_file.read_text()) if exit_file.exists() else 0
if code:
    print("fake extractor failure", file=sys.stderr)
    sys.exit(code)
response = directory / f"response-{call}.json"
print(response.read_text() if response.exists() else json.dumps(
    {"type": "result", "is_error": False, "total_cost_usd": 0.0, "structured_output": {"candidates": []}}))
