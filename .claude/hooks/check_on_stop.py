"""Claude Code Stop hook: run scripts/check.py before the agent ends a turn with changed Python files.

Exit code 2 sends the failing output back to the agent, which then has to fix it or say plainly that the
checks are red (for example, a failing test written first and waiting for the user's approval). The
second stop in a row (``stop_hook_active``) is let through, so a deliberately red state never loops.
The fingerprint of the last green run is kept in ``.git`` so an unchanged tree is not re-checked.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAMP = ROOT / ".git" / "claude-check-ok"
TAIL_LINES = 40


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def tree_fingerprint() -> str | None:
    """Hash of uncommitted Python changes, or None when there are none."""
    paths = (line[3:].split(" -> ")[-1].strip('"') for line in git("status", "--porcelain", "-uall").splitlines())
    changed = [path for path in paths if path.endswith(".py")]
    if not changed:
        return None
    digest = hashlib.sha256(git("rev-parse", "HEAD").encode())
    for name in sorted(changed):
        path = ROOT / name
        digest.update(name.encode())
        digest.update(path.read_bytes() if path.is_file() else b"<deleted>")
    return digest.hexdigest()


def python_exe() -> Path:
    windows = ROOT / ".venv" / "Scripts" / "python.exe"
    return windows if windows.exists() else ROOT / ".venv" / "bin" / "python"


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        event = {}
    if event.get("stop_hook_active"):
        return 0

    fingerprint = tree_fingerprint()
    if fingerprint is None:
        return 0
    if STAMP.is_file() and STAMP.read_text(encoding="utf-8") == fingerprint:
        return 0

    result = subprocess.run(
        [str(python_exe()), "scripts/check.py"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode == 0:
        STAMP.write_text(fingerprint, encoding="utf-8")
        return 0

    tail = "\n".join((result.stdout + result.stderr).splitlines()[-TAIL_LINES:])
    print(
        "scripts/check.py failed on the current working tree (full output: check.log). Fix it, or if the red "
        "state is intended, say so explicitly in your report.\n" + tail,
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
