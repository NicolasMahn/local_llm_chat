"""Saved conversations: one JSON file per chat, in the messages format the API takes."""

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from gi.repository import GLib

from client import plain_text

FOLDER = Path(GLib.get_user_data_dir()) / "local-llm" / "chats"


class Chat:
    def __init__(self, messages=None, created=None, chat_id=None):
        self.id = chat_id or uuid.uuid4().hex
        self.created = created or datetime.now().astimezone().isoformat(timespec="seconds")
        self.messages = messages or []

    @property
    def path(self):
        return FOLDER / f"{self.id}.json"

    @property
    def title(self):
        for message in self.messages:
            if message["role"] == "user":
                return plain_text(message["content"]).strip()[:60] or "Attachment"
        return "New chat"

    def save(self):
        FOLDER.mkdir(parents=True, exist_ok=True)
        partial = self.path.with_suffix(".partial")
        partial.write_text(json.dumps(
            {"created": self.created, "messages": self.messages}, ensure_ascii=False))
        os.replace(partial, self.path)

    def delete(self):
        self.path.unlink(missing_ok=True)


def load_all():
    """Every saved chat, newest first."""
    chats = []
    for path in FOLDER.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue  # A damaged file should not hide the other chats.
        chats.append(Chat(data["messages"], data["created"], path.stem))
    return sorted(chats, key=lambda chat: chat.created, reverse=True)
