#!/usr/bin/env bash
# Install the shared tmux setup (config, cheatsheet, fish SSH auto-attach) for this user.
# Canonical copy lives in my private laptop dotfiles repo; this is the shipped copy.
# Per-machine overrides go in an untracked ~/.config/tmux/local.conf.
#   ./dotfiles/install-tmux.sh            install (backs up anything it replaces)
#   ./dotfiles/install-tmux.sh --dry-run  show what would change
# It never touches a running tmux server: the new config loads on the next server start.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
dry=false; [[ "${1:-}" == --dry-run ]] && dry=true
ts="$(date +%Y%m%d-%H%M%S)"
G=$'\e[32m'; Y=$'\e[33m'; N=$'\e[0m'

install_file() {  # src dest [mode]
    local src="$1" dest="$2" mode="${3:-644}"
    if [[ -f "$dest" ]] && cmp -s "$src" "$dest"; then
        echo "${Y}same${N}     $dest"; return
    fi
    if $dry; then echo "${G}would install${N} $dest"; return; fi
    mkdir -p "$(dirname "$dest")"
    [[ -e "$dest" ]] && cp -a "$dest" "$dest.bak.$ts" && echo "${Y}backup${N}   $dest.bak.$ts"
    install -m "$mode" "$src" "$dest"
    echo "${G}installed${N} $dest"
}

install_file "$here/tmux/tmux.conf"      "$HOME/.config/tmux/tmux.conf"
install_file "$here/tmux/cheatsheet.tsv" "$HOME/.config/tmux/cheatsheet.tsv"
install_file "$here/tmux/cheatsheet.sh"  "$HOME/.config/tmux/cheatsheet.sh" 755
install_file "$here/fish/conf.d/tmux-ssh-attach.fish" "$HOME/.config/fish/conf.d/tmux-ssh-attach.fish"
install_file "$here/fish/functions/tmux-help.fish"    "$HOME/.config/fish/functions/tmux-help.fish"

if ! $dry && [[ ! -d "$HOME/.tmux/plugins/tpm" ]]; then
    echo "TPM is cloned automatically the next time a tmux server starts; then press Ctrl-b I."
fi
