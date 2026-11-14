# Claude Assistant directory

A general-purpose directory (`~/Assistant`) for Claude Code sessions that aren't about one project: emails, warranty claims, research, planning. A user service keeps [Remote Control](https://code.claude.com/docs/en/remote-control) running on it, so new sessions can be opened from the Claude mobile app or claude.ai/code without SSH.

## Layout

| Path | Purpose |
| --- | --- |
| `CLAUDE.md` | How sessions here should behave (draft emails before sending, keep matter files, etc.) |
| `INDEX.md` | Map of projects and important directories on the machine (locations of secrets only, never contents) |
| `matters/` | One file per ongoing real-world issue, so a later session can pick it up cold |
| `scratch/` | Throwaway files |

!!! note "Memory is per directory"
    Auto-memory lives under `~/.claude/projects/<path-slug>/memory/`, so sessions in `~/Assistant` don't see memories saved from other repos. Copy general ones (name, preferences) into `~/.claude/projects/-home-<user>-Assistant/memory/` and add them to its `MEMORY.md`.

## Remote Control service

Remote Control refuses to start in an untrusted directory. Run `claude` in `~/Assistant` once and accept the trust dialog first.

`~/.config/systemd/user/claude-assistant-rc.service`:

```ini
[Unit]
Description=Claude Code Remote Control server for ~/Assistant
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory=%h/Assistant
# Login fish so spawned sessions get the same PATH (fnm node, bun, cargo, ...) as a terminal.
ExecStart=/usr/bin/fish -lc 'exec ~/.local/bin/claude remote-control --name Assistant'
Restart=always
RestartSec=15

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now claude-assistant-rc
loginctl enable-linger $USER   # start at boot without a login
```

The machine then shows up as **Assistant** in the Claude app, and each new session starts in `~/Assistant`.

!!! warning
    When the Claude login expires, Remote Control stops connecting. Run `/login` in any `claude` session, then `systemctl --user restart claude-assistant-rc`.

Useful commands:

```bash
systemctl --user status claude-assistant-rc
journalctl --user -u claude-assistant-rc -f
```
