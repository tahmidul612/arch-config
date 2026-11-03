#!/usr/bin/env bash
# Searchable tmux cheatsheet: curated entries (cheatsheet.tsv, incl. plugin keys
# that tmux itself doesn't know about) + the live bindings of the running server.
# Bound to Ctrl-b ?; also works outside tmux (fish: tmux-help).
set -u
here="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
sheet="$here/cheatsheet.tsv"

c_sec=$'\e[38;2;199;146;234m'   # Night Owl purple
c_key=$'\e[38;2;130;170;255m'   # blue
c_dim=$'\e[38;2;99;119;119m'    # comment grey
c_off=$'\e[0m'

row() { printf '%s%-14s%s %s%-26s%s %s\n' "$c_sec" "$1" "$c_off" "$c_key" "$2" "$c_off" "$3"; }

{
    # Curated sheet
    while IFS=$'\t' read -r sec keys action; do
        [[ -z "$sec" || "$sec" == \#* ]] && continue
        row "$sec" "$keys" "$action"
    done < "$sheet"

    # Live bindings from the running server (catches anything not in the sheet)
    if tmux info >/dev/null 2>&1; then
        tmux list-keys -N 2>/dev/null | while read -r k rest; do
            [[ "$k" == C-b ]] && { k="Ctrl-b ${rest%% *}"; rest="${rest#* }"; }
            row "live" "$k" "$(sed 's/^ *//' <<<"$rest")"
        done
        tmux list-keys -T off 2>/dev/null | awk '{k=$4; $1=$2=$3=$4=""; sub(/^ +/,""); print k "\t" $0}' |
            while IFS=$'\t' read -r k cmd; do row "live:off" "$k" "${c_dim}${cmd}${c_off}"; done
        tmux list-keys -T copy-mode-vi 2>/dev/null |
            awk '{i=4; if ($2=="-r") i=5; k=$i; for(j=1;j<=i;j++) $j=""; sub(/^ +/,""); print k "\t" $0}' |
            while IFS=$'\t' read -r k cmd; do row "live:copy" "$k" "${c_dim}${cmd}${c_off}"; done
    fi
} | fzf --ansi --reverse --no-sort --exact --tiebreak=index \
        --prompt 'search> ' \
        --header 'Type to filter (e.g. "split", "sidebar", "copy")  ·  Esc closes  ·  live* = from the running server' \
        --bind 'enter:ignore' --no-info --color='header:#637777,prompt:#82aaff,pointer:#c792ea'
exit 0
