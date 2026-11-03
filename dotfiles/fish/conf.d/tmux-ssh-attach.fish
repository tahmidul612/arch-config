# Interactive SSH logins land in tmux session "main" (created if missing).
# Skipped for non-interactive ssh (scripts, scp, `ssh host cmd`), inside tmux,
# or with NOTMUX set:  ssh -t HOST 'NOTMUX=1 fish'
# Not exec'd: detaching (Ctrl-b d) drops you to a plain shell on the host.
if status is-interactive
    and set -q SSH_CONNECTION
    and not set -q TMUX
    and not set -q NOTMUX
    and test "$TERM" != dumb
    and command -q tmux
    tmux new -A -s main
end
