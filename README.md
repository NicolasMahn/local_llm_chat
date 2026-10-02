# local_llm_chat

A GNOME chat window for the models served by
[local_llm](https://github.com/NicolasMahn/local_llm), and for any model
[pi](https://pi.dev) can reach. Replies are shown as Markdown: headings,
lists, tables and code.

The window runs pi in the background (`pi --mode rpc`) and leaves the talking
to models to it. `pi-extension/modes.ts` gives pi two modes, switched below
the message field:

- **Chat**: an assistant that reads web pages, and can browse them like a
  person: open a page, click, type, scroll and go back (`browser.ts`). The
  browser is your installed Chrome or Chromium, headless, with an empty profile
  for each chat, so pages see none of your accounts; models that can see
  pictures look at screenshots, the others at a numbered list of what the page
  offers. It asks you before submitting a form. When you ask for work on files
  or code, it asks to switch to work mode, and you confirm.
- **Work**: pi's own coding agent. It starts in `~/llm-work` but can reach your
  other files; with a hosted model, what it reads goes to that provider.

The web tools only reach public addresses, never this computer or the local
network, redirects included (`web.ts`, and `proxy.ts` for the browser).

Either mode can be **private**: local models only, and nothing reaches the
internet. A private chat has no web tools, and in private work every command
runs in a bubblewrap sandbox without network access or the desktop's message
bus (`sandbox.ts`). Private needs the model gateway on this computer. A new
chat keeps the mode and privacy of the one before.

A local model is always the default. The local models come from the gateway
at `http://127.0.0.1:8400` with the key in `~/.config/local-llm/api-key`,
both of which local_llm sets up; set `LOCAL_LLM_GATEWAY` to use one on
another machine (private chats then cannot run). Other providers, such as
Codex or OpenRouter, are the ones you have logged in to in pi (`pi`, then
`/login`). Pictures (PNG, JPEG, WebP,
GIF, up to 20 MB) go to models that can see them. The camera button next to the
paperclip opens GNOME's screenshot tool, as the Print key does, on any monitor;
the drawer steps aside meanwhile and comes back with the screenshot attached.

Past chats are pi sessions, kept in `~/.local/share/local-llm-chat/sessions/`
apart from your other pi sessions.

It needs GTK 4 and libadwaita with their Python bindings, markdown-it-py,
bubblewrap, pi, and Chrome or Chromium for browsing. On Fedora:

    sudo dnf install python3-gobject libadwaita libportal python3-markdown-it-py bubblewrap
    sudo npm install -g @earendil-works/pi-coding-agent
    ./install.sh

`install.sh` installs the pi extension's packages, puts "Local LLM Chat" in
the app grid and adds a GNOME Shell
extension (in `gnome-extension/`, loaded at the next login) with a chat button
in the top bar. The button slides the chat in over the right edge of the
screen, like a drawer, and out again, as do the arrow at its top left,
Escape and clicking another window; hidden, it keeps its state. The pin in
the chat list widens the drawer and keeps the list beside the conversation.
It has no close button, so reaching for another window's cannot quit it. It
stays on every workspace, and out of Alt+Tab and the dock. `./chat.py` runs it
without installing.
