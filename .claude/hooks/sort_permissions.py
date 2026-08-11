"""Keep ``permissions.allow`` in ``.claude/settings.json`` sorted.

CLAUDE.md requires the list in ASCII order, but newly approved permissions
are appended to the end by the harness itself, outside any tool event a
PostToolUse hook could see. Running this from a SessionStart hook repairs
any drift at every session boundary. Writes only when the order is wrong,
so the common case is a no-op.
"""

import json
import os
from pathlib import Path


def main() -> None:
    """Sort the allow list in place, preserving all other settings.

    Reads the settings file under ``$CLAUDE_PROJECT_DIR`` (falling back to
    the current directory), and rewrites it with 2-space indentation and a
    trailing newline only if the list was out of order.
    """
    path = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")) / ".claude" / "settings.json"
    settings = json.loads(path.read_text())
    allow = settings.get("permissions", {}).get("allow")
    if not allow:
        return
    ordered = sorted(allow)
    if ordered != allow:
        settings["permissions"]["allow"] = ordered
        path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
        print(f"sort_permissions: re-sorted {len(ordered)} permissions.allow entries")


if __name__ == "__main__":
    main()
