#!/usr/bin/env bash
# Adds the chat to the app grid for the current user. Safe to run again.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
app_id=io.github.nicolasmahn.LocalLlmChat
data="${XDG_DATA_HOME:-$HOME/.local/share}"

# Desktop files need an absolute Exec path; quoted in case the repo path has spaces.
mkdir -p "$data/applications"
sed "s|^Exec=chat.py|Exec=\"$repo/chat.py\"|" "$repo/data/$app_id.desktop" \
    > "$data/applications/$app_id.desktop"
if command -v update-desktop-database > /dev/null; then
    update-desktop-database -q "$data/applications"
fi

# The pi extension's own packages (Playwright, to drive the installed Chrome).
npm install --prefix "$repo/pi-extension" --no-fund --no-audit --silent

# The extension slides the chat in from the right edge of the screen.
uuid=local-llm-chat@nicolas
mkdir -p "$data/gnome-shell/extensions"
ln -sfn "$repo/gnome-extension" "$data/gnome-shell/extensions/$uuid"
# gnome-extensions only knows extensions the running shell has loaded, so edit the list directly.
enabled="$(gsettings get org.gnome.shell enabled-extensions)"
if [[ "$enabled" != *"'$uuid'"* ]]; then
    if [[ "$enabled" == *"[]" ]]; then
        enabled="['$uuid']"
    else
        enabled="${enabled%]}, '$uuid']"
    fi
    gsettings set org.gnome.shell enabled-extensions "$enabled"
fi
echo "Installed. Local LLM Chat is in the app grid; log out and in for its top-bar button."
