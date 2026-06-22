#!/usr/bin/env python3
"""PostToolUse hook: keep the repo consistent after edits.

- Editing a proto/*.proto file -> regenerate stubs into both services.
- Editing a *.py file (outside generated/) -> ruff format + lint --fix.

Failures here are advisory; we never exit 2 (non-blocking on PostToolUse).
"""
import json
import os
import subprocess
import sys


def project_root() -> str:
    root = os.environ.get("CLAUDE_PROJECT_DIR")
    if root:
        return root
    # .claude/hooks/post_edit.py -> project root
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return

    file_path = data.get("tool_input", {}).get("file_path", "")
    if not file_path:
        return

    root = project_root()

    if file_path.endswith(".proto") and "/proto/" in file_path:
        subprocess.run(["bash", os.path.join(root, "scripts", "regen_proto.sh")], check=False)
        return

    if file_path.endswith(".py") and "/generated/" not in file_path:
        subprocess.run(["ruff", "format", file_path], check=False)
        subprocess.run(["ruff", "check", "--fix", file_path], check=False)


if __name__ == "__main__":
    main()
