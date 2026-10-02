"""The pi process behind the chat: one long-lived `pi --mode rpc`, spoken to in JSON lines."""

import itertools
import json
import os
import subprocess
import threading
from pathlib import Path

from gi.repository import GLib

EXTENSION = Path(__file__).resolve().parent / "pi-extension" / "modes.ts"
WORK_FOLDER = Path.home() / "llm-work"
# Apart from pi's own sessions, so the chat list only shows chats.
SESSIONS = Path(GLib.get_user_data_dir()) / "local-llm-chat" / "sessions"


class Pi:
    """Runs pi and calls on_event(event) on the GTK thread for everything it reports."""

    def __init__(self, on_event):
        WORK_FOLDER.mkdir(exist_ok=True)
        SESSIONS.mkdir(parents=True, exist_ok=True)
        self.on_event = on_event
        self.waiting = {}  # request id -> callback for its response
        self.ids = itertools.count()
        self.process = subprocess.Popen(
            ["pi", "--mode", "rpc", "--no-extensions", "--extension", str(EXTENSION),
             "--no-skills", "--session-dir", str(SESSIONS)],
            # Work mode works here; the other modes have no file tools.
            cwd=WORK_FOLDER,
            env=os.environ | {"PI_TELEMETRY": "0", "PI_SKIP_VERSION_CHECK": "1"},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        threading.Thread(target=self.read, daemon=True).start()

    def send(self, command, on_response=None):
        """Sends a command; on_response, if given, gets pi's response on the GTK thread."""
        if on_response:
            command = {**command, "id": str(next(self.ids))}
            self.waiting[command["id"]] = on_response
        try:
            self.process.stdin.write(json.dumps(command) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass  # pi has exited; its "exit" event tells the window

    def prompt(self, text, images=(), on_response=None):
        self.send({"type": "prompt", "message": text, "images": list(images)}, on_response)

    def stop(self):
        self.process.terminate()

    def read(self):
        """Worker thread: hands every line pi writes to the GTK thread."""
        for line in self.process.stdout:
            GLib.idle_add(self.dispatch, json.loads(line))
        GLib.idle_add(self.dispatch, {"type": "exit"})

    def dispatch(self, message):
        # Dialog requests from pi carry ids too; only responses answer ours.
        if message["type"] == "response" and message.get("id") in self.waiting:
            self.waiting.pop(message["id"])(message)
        else:
            self.on_event(message)
