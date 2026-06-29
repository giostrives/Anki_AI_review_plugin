"""
Conversation persistence.

Every card review is one conversation; all conversations from a single review
session are appended (one JSON line each) to a per-session JSONL file under a
default folder. Nothing consumes this data yet — it's groundwork for review
history, analysis, or export.
"""
import json
import os
from datetime import datetime

_addon_dir = os.path.dirname(os.path.abspath(__file__))


def default_dir(config):
    """Return the folder where conversations are stored, creating it if needed.

    Defaults to `<addon>/user_files/conversations` (Anki preserves `user_files`
    across add-on updates). Overridable via config["conversations_dir"].
    """
    path = config.get("conversations_dir") or os.path.join(
        _addon_dir, "user_files", "conversations"
    )
    os.makedirs(path, exist_ok=True)
    return path


def new_session_path(config):
    """Build (but don't create) the JSONL path for a new review session."""
    stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    return os.path.join(default_dir(config), f"session_{stamp}.jsonl")


def append_conversation(path, record):
    """Append one conversation as a JSON line. Best-effort: never raises."""
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        # Persistence must never break a review.
        print(f"[AI Reviewer] Failed to save conversation: {e}")
