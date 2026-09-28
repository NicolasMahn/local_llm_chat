# local_llm_chat

A GNOME chat window for the models served by
[local_llm](https://github.com/NicolasMahn/local_llm). Past chats sit in a
sidebar, saved as JSON in `~/.local/share/local-llm/chats/`.

Pictures and videos (PNG, JPEG, WebP, GIF, MP4, WebM, up to 50 MB) go to
`gemma-4-26b`, the one model there that can see; with another model selected
the window says so instead. While `qwen3-asr` runs, a microphone button
records until pressed again and puts the transcript in the message field, and
"let's talk" holds a spoken conversation: it listens until you pause, sends
what you said, reads the answer aloud and listens again.

It needs GTK 4 and libadwaita with their Python bindings, numpy, PipeWire's
`pw-record` and speech-dispatcher's `spd-say`. On Fedora:

    sudo dnf install python3-gobject libadwaita python3-numpy pipewire-utils speech-dispatcher-utils
    ./install.sh

`install.sh` puts "Local LLM Chat" in the app grid; the local_llm top-bar
extension opens it too. `./chat.py` runs it without installing.

The chat talks to the gateway at `http://127.0.0.1:8400` with the key in
`~/.config/local-llm/api-key`, both of which local_llm sets up. To use a
gateway on another machine, copy the key to the same file there and set
`LOCAL_LLM_GATEWAY`, e.g. `LOCAL_LLM_GATEWAY=http://fedora.local:8400`.
The traffic is plain HTTP, so only do that on a network you trust.
