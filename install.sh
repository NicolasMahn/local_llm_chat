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
echo "Installed. Local LLM Chat is in the app grid."
