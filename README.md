# local_llm_chat

A GNOME chat window for the models served by
[local_llm](https://github.com/NicolasMahn/local_llm). Past chats sit in a
sidebar, saved as JSON in `~/.local/share/local-llm/chats/`.

Pictures and videos (PNG, JPEG, WebP, GIF, MP4, WebM, up to 50 MB) go to
`gemma-4-26b`, the one model there that can see; with another model selected
the window says so instead.

It needs GTK 4 and libadwaita with their Python bindings. On Fedora:

    sudo dnf install python3-gobject libadwaita
    ./install.sh

`install.sh` puts "Local LLM Chat" in the app grid; the local_llm top-bar
extension opens it too. `./chat.py` runs it without installing.

The chat talks to the gateway at `http://127.0.0.1:8400` with the key in
`~/.config/local-llm/api-key`, both of which local_llm sets up. To use a
gateway on another machine, copy the key to the same file there and set
`LOCAL_LLM_GATEWAY`, e.g. `LOCAL_LLM_GATEWAY=http://fedora.local:8400`.
The traffic is plain HTTP, so only do that on a network you trust.
