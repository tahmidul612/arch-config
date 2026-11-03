# Put the tmux-aware xclip shim (~/.config/tmux/bin/xclip) first in PATH, so that a
# long-running Claude Code in tmux still pastes images after an ssh -X reconnect.
# -g, not universal: nothing gets saved to fish_variables.
if test -x ~/.config/tmux/bin/xclip
    fish_add_path -g -p -m ~/.config/tmux/bin
end
