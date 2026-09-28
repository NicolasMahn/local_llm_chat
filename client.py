"""Calls to the local_llm gateway."""

import json
import os
import urllib.request

from gi.repository import GLib

GATEWAY = os.environ.get("LOCAL_LLM_GATEWAY", "http://127.0.0.1:8400")
# Only these models can look at pictures and videos (vLLM loads their vision part).
VISION_MODELS = {"gemma-4-26b"}
KEY_FILE = os.path.join(GLib.get_user_config_dir(), "local-llm", "api-key")


def request(path, data=None):
    # Read on every call: the llm script creates the key on its first start.
    with open(KEY_FILE) as key:
        headers = {"authorization": f"Bearer {key.read().strip()}"}
    if data is not None:
        headers["content-type"] = "application/json"
    return urllib.request.Request(GATEWAY + path, data=data, headers=headers)


def running_models():
    with urllib.request.urlopen(request("/v1/models"), timeout=3) as response:
        return [model["id"] for model in json.load(response)["data"]]


def for_model(model, messages):
    """The conversation as this model can take it: pictures and videos only for vision models."""
    if model in VISION_MODELS:
        return messages
    return [
        {**message, "content": text_of(message["content"])} for message in messages
    ]


def plain_text(content):
    """The words of a message, whose content is a string or a list of parts."""
    if isinstance(content, str):
        return content
    return " ".join(part["text"] for part in content if part["type"] == "text")


def text_of(content):
    """The message for a model without eyes: its words, and a note where media was."""
    has_media = not isinstance(content, str) and any(part["type"] != "text" for part in content)
    return plain_text(content) + (" [an attachment only a vision model can see]" if has_media else "")


def stream_reply(model, messages, on_text, on_thinking):
    """Streams the reply, calling on_text with the answer so far; returns the full answer."""
    payload = json.dumps({"model": model, "messages": for_model(model, messages), "stream": True})
    answer = ""
    with urllib.request.urlopen(request("/v1/chat/completions", payload.encode())) as response:
        for line in response:
            line = line.decode().strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            delta = json.loads(line[6:])["choices"][0]["delta"]
            if delta.get("content"):
                answer += delta["content"]
                # Qwen leaves blank lines between its thinking and the answer.
                on_text(answer.lstrip())
            elif not answer and delta.get("reasoning"):
                on_thinking()
    return answer.lstrip()
