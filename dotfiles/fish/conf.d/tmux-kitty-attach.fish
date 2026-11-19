# New kitty windows/tabs land in tmux: attach to the most recent session,
# or create "main" if no tmux server/session exists yet.
# Skipped over SSH (tmux-ssh-attach.fish handles that), inside tmux,
# for non-interactive fish, or with NOTMUX set:  NOTMUX=1 kitty
# Not exec'd: detaching (Ctrl-b d) drops you to a plain shell in kitty.
if status is-interactive
    and set -q KITTY_WINDOW_ID
    and not set -q SSH_CONNECTION
    and not set -q TMUX
    and not set -q NOTMUX
    and command -q tmux
    tmux attach 2>/dev/null; or tmux new -s main
end
