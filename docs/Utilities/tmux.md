# tmux

[tmux](https://github.com/tmux/tmux) is a terminal multiplexer. It splits one terminal into windows and panes, and keeps sessions running after you close the terminal or lose an SSH connection. It replaced byobu here: byobu is a wrapper around tmux, and plain tmux with one config does the same job with fewer layers.

The same config runs on my laptop and my home server. It keeps tmux's **default bindings** (prefix ++ctrl+b++), so any tmux tutorial applies as-is, and adds:

- a searchable cheatsheet on ++ctrl+b++ ++question++
- Claude Code awareness: an agent sidebar and status-bar icons for panes waiting on you
- comfortable nesting over SSH (++f12++), auto-attach on SSH login, and a different accent colour per machine
- sessions that survive reboots

## Installation

```bash
sudo pacman -S tmux fzf git
paru -S sesh-bin          # optional: project/session picker on Ctrl-b S (uses zoxide)
```

!!! warning "Coming from byobu"
    tmux is installed as a *dependency* of byobu. Mark it explicit before removing byobu, or `pacman -Rns` takes tmux with it:

    ```bash
    sudo pacman -D --asexplicit tmux
    sudo pacman -Rns byobu
    rm -rf ~/.config/byobu
    ```

Then install the config from this repository and install the plugins:

```bash
./dotfiles/install-tmux.sh --dry-run   # preview
./dotfiles/install-tmux.sh             # copies config, cheatsheet and fish snippets (backs up old files)
tmux kill-server                       # only if an old (e.g. byobu) server is running; ends its sessions
tmux new -s main                       # first start clones TPM
# inside tmux: Ctrl-b I                # installs plugins; fingers and the sidebar run an install wizard
```

Finally, wire the agent sidebar into Claude Code (once per machine):

```text
/plugin marketplace add ~/.tmux/plugins/tmux-agent-sidebar
/plugin install tmux-agent-sidebar@hiroppy
/reload-plugins
```

## Files

| File | Installed to | Purpose |
| --- | --- | --- |
| [`dotfiles/tmux/tmux.conf`](https://github.com/tahmidul612/arch-config/blob/main/dotfiles/tmux/tmux.conf) | `~/.config/tmux/` | The config |
| `dotfiles/tmux/cheatsheet.tsv` | `~/.config/tmux/` | Curated cheatsheet entries, including plugin keys |
| `dotfiles/tmux/cheatsheet.sh` | `~/.config/tmux/` | fzf viewer for the cheatsheet plus live bindings |
| `dotfiles/fish/conf.d/tmux-ssh-attach.fish` | `~/.config/fish/conf.d/` | Auto-attach on interactive SSH login |
| `dotfiles/fish/functions/tmux-help.fish` | `~/.config/fish/functions/` | `tmux-help` opens the cheatsheet outside tmux |
| *(not in the repo)* `local.conf` | `~/.config/tmux/` | Per-machine overrides, see below |

### Per-machine settings

`tmux.conf` sources `~/.config/tmux/local.conf` if it exists. Use it for anything that differs between machines, for example:

```tmux
set -g @accent "#82aaff"                       # status-bar accent
set -g pane-active-border-style "fg=#82aaff"
set -g popup-border-style "fg=#82aaff"
set -g @sidebar_notifications 'on'             # desktop notifications (needs a notify daemon)
```

Plugins live in `~/.tmux/plugins/`, managed by [TPM](https://github.com/tmux-plugins/tpm).

## Learning the keys

++ctrl+b++ ++question++ opens a searchable cheatsheet in a popup. It shows tmux's defaults, the extras below, every plugin's keys (including keys *inside* the sidebar and hint mode, which tmux itself doesn't know about), and the live bindings of the running server. Type to filter, e.g. `split`, `sidebar`, `copy`. Outside tmux, run `tmux-help`.

The extras on top of the defaults:

| Keys | Action |
| --- | --- |
| ++ctrl+b++ ++question++ | Cheatsheet (search) |
| ++ctrl+b++ ++r++ | Reload `tmux.conf` |
| ++ctrl+b++ ++bar++ / ++minus++ | Split left/right / top/bottom in the current directory |
| ++ctrl+b++ ++shift+s++ | Session and project picker (sesh, or plain fzf without it) |
| ++ctrl+b++ ++shift+p++ | Toggle a scratch terminal popup that keeps its state |
| ++ctrl+b++ ++a++ / ++shift+a++ | Agent sidebar in this window / all windows |
| ++ctrl+b++ ++shift+f++ | Hint-copy paths, URLs, hashes and IPs (tmux-fingers) |
| ++ctrl+b++ ++ctrl+s++ / ++ctrl+r++ | Save / restore sessions now (tmux-resurrect) |
| ++f12++ | Laptop only: turn the outer tmux off for a nested remote tmux |

tmux 3.8 also has native floating panes: ++ctrl+b++ ++asterisk++ opens one, ++ctrl+b++ ++at++ toggles a pane between floating and tiled, ++ctrl+b++ ++g++ moves it.

## Claude Code

- **Status-bar icons.** Each window shows `●` when Claude is waiting on you, `⏳` while it works and `✗` on error. The icons come from the pane options that tmux-agent-sidebar's hooks set, so they need the Claude Code plugin step above.
- **Agent sidebar.** ++ctrl+b++ ++a++ shows every Claude pane across all sessions with its latest prompt, tool calls and subagents. ++enter++ jumps to a pane, and ++n++ spawns a new git worktree with Claude in it. It is hidden by default to save width. Desktop notifications are off by default; turn them on in `local.conf` on machines with a notification daemon.
- **Keys.** `extended-keys` passes ++shift+enter++ through to Claude Code, including in a nested tmux over SSH.

## SSH and nesting

- **Auto-attach.** Interactive SSH logins run `tmux new -A -s main`. Non-interactive `ssh host cmd`, scp and scripts are left alone. To skip it: `ssh -t host 'NOTMUX=1 fish'`. Detaching (++ctrl+b++ ++d++) drops you to a normal shell on the host.
- **Nesting.** On the laptop, ++f12++ turns the local tmux off: its status bar shows `OFF` and every key, including ++ctrl+b++, goes to the remote tmux. ++f12++ again turns it back on. Only a locally started tmux binds ++f12++, so you can't switch off both layers by accident.
- **Which host am I on?** The host name is in the status bar, in that machine's accent colour (set in `local.conf`).
- **Clipboard.** Copying in copy mode (or from Claude Code) on the remote goes through OSC 52 to kitty, and lands in the local clipboard.

!!! note "Mosh"
    [Mosh](mosh.md) can't pass extended keys or kitty graphics. Over mosh, use ++ctrl+j++ instead of ++shift+enter++ in Claude Code, and yazi shows no image previews. Plain `ssh` (or `kitten ssh`) supports both.

## Usage

```bash
tmux new -A -s work       # attach to "work", creating it if missing
tmux ls                   # list sessions
tmux kill-session -t work
tmux new -d -s upgrade 'paru -Syu' && tmux attach -t upgrade   # background job
```
