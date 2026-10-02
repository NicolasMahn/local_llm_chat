"""Past chats: the pi session files the chat window keeps, newest first."""

import json
from dataclasses import dataclass
from pathlib import Path

from gi.repository import Gio

from pi_client import SESSIONS


@dataclass
class Session:
    path: Path
    title: str
    created: str  # ISO 8601
    work: bool
    private: bool

    def trash(self):
        Gio.File.new_for_path(str(self.path)).trash(None)


def load_all():
    sessions = []
    for path in SESSIONS.glob("*.jsonl"):
        try:
            session = read(path)
        except (OSError, ValueError, KeyError):
            continue  # A damaged file should not hide the other chats.
        if session:
            sessions.append(session)
    return sorted(sessions, key=lambda session: session.created, reverse=True)


def read(path):
    """The session in a file, or None while it has no message of yours yet."""
    created, name, first_words, mode = None, None, None, {}
    with open(path) as file:
        for line in file:
            entry = json.loads(line)
            if entry["type"] == "session":
                created = entry["timestamp"]
            elif entry["type"] == "custom" and entry.get("customType") == "mode":
                mode = entry["data"]
            elif entry["type"] == "session_info":
                name = entry.get("name") or name
            elif first_words is None and entry["type"] == "message" and entry["message"]["role"] == "user":
                first_words = text_of(entry["message"]["content"]).strip()[:60] or "Picture"
    if first_words is None:
        return None
    if created is None:
        raise ValueError(f"{path} has no session header")
    return Session(path, name or first_words, created, mode.get("mode") == "work", mode.get("private", False))


def text_of(content):
    """The words of a message, whose content is a string or a list of parts."""
    if isinstance(content, str):
        return content
    return " ".join(part["text"] for part in content if part["type"] == "text")
