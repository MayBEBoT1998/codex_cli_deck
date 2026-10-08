#!/usr/bin/env python3
"""Official Codex notify callback: one JSON argument, local event files only."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codex_deck.core import emit_event


def main():
    if len(sys.argv) != 3:
        return 2
    try:
        event = json.loads(sys.argv[2])
        if not isinstance(event, dict):
            return 2
        if event.get("type") != "agent-turn-complete":
            return 0
        # Keep the turn identity and a bounded preview. Never execute event content.
        emit_event(sys.argv[1], {
            "type": "agent-turn-complete",
            "turn-id": str(event.get("turn-id", ""))[:256],
            "thread-id": str(event.get("thread-id", ""))[:256],
            "last-assistant-message": str(event.get("last-assistant-message", ""))[:4000],
        })
        return 0
    except (ValueError, OSError):
        return 2


if __name__ == "__main__":
    sys.exit(main())
