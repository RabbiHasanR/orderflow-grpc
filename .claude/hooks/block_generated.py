#!/usr/bin/env python3
"""PreToolUse hook: block hand-edits to protoc-generated code.

Exits 2 to deny the tool call when the target path is under a `generated/` dir.
"""
import json
import sys


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return  # Don't block on malformed input.

    file_path = data.get("tool_input", {}).get("file_path", "")
    if "/generated/" in file_path or file_path.endswith("/generated"):
        sys.stderr.write(
            "Refusing to edit generated protoc output. "
            "Edit proto/*.proto and run scripts/regen_proto.sh instead.\n"
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
