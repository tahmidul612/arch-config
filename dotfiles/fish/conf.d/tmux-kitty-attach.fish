# New kitty windows/tabs land in tmux. Each one gets its own session:
# reattach the most recently used *detached* session (e.g. left over after
# closing kitty), otherwise start a new one ("main" if none exist yet).
# Sessions already shown in another window are never reused, so a new tab
# doesn't mirror the one you're in.
# Skipped over SSH (tmux-ssh-attach.fish handles that), inside tmux,
# for non-interactive fish, or with NOTMUX set:  NOTMUX=1 kitty
# Not exec'd: detaching (Ctrl-b d) drops you to a plain shell in kitty.
if status is-interactive
    and set -q KITTY_WINDOW_ID
    and not set -q SSH_CONNECTION
    and not set -q TMUX
    and not set -q NOTMUX
    and command -q tmux
    set -l __sessions (tmux list-sessions -F '#{session_attached}	#{session_last_attached}	#{session_name}' 2>/dev/null)
    set -l __detached (printf '%s\n' $__sessions | awk -F '\t' '$1 == 0' | sort -t \t -k2,2nr | head -n1 | cut -f3-)
    if test -n "$__detached"
        tmux attach -t "=$__detached"
    else if test -z "$__sessions"
        tmux new -s main
    else
        tmux new
    end
end
