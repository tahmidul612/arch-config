# Claude Code

How [Claude Code](https://code.claude.com/docs) is configured on this machine, and how to check that the configuration is still healthy after updates.

## Where the config lives

| Path | What it holds |
| --- | --- |
| `~/.claude/settings.json` | User settings: permission mode, hooks, plugins, statusline, auto mode rules |
| `~/.claude/settings.local.json` | Per-machine overrides, such as allow rules for individual MCP tools |
| `~/.claude/hooks/` | The secret-guard hook scripts described below |
| `~/.claude/agents/` | Custom subagents |
| `<repo>/.claude/` | Project skills and settings, such as this repo's `full-system-upgrade` skill |

A second profile can live in another directory and be started with `CLAUDE_CONFIG_DIR=~/.claude-<name> claude`. It is a separate copy, not an overlay, so changes to hooks or auto mode have to be made in both profiles.

## Permission mode

The default mode is `auto`: a classifier reviews each action, and only risky ones are blocked or prompted. The classifier's picture of the environment comes from `autoMode` in user settings:

```json
"autoMode": {
  "environment": ["$defaults"]
}
```

!!! warning "Keep `$defaults` in every `autoMode` list"
    Each `autoMode` section (`environment`, `allow`, `soft_deny`, `hard_deny`) **replaces** the built-in list unless it contains the literal `"$defaults"`. `/auto-mode-setup` drafts entries from the project and sessions it ran in. A draft saved without `"$defaults"` pins the *Trusted repo* to that one project in every other repo, and it misses later improvements to the built-in rules. Add your own entries after `"$defaults"` and write them so they stay true, rather than describing the current state ("no remotes yet").

!!! note
    `autoMode` is only read from user (or managed) settings. Auto mode ignores it in a repo's `.claude/settings.json` and `.claude/settings.local.json`, so a cloned repo can't widen what auto mode allows.

Useful commands:

```bash
claude auto-mode config     # effective rules: your entries merged with the defaults
claude auto-mode defaults   # the built-in rules for this version
claude auto-mode critique   # AI review of your custom rules
```

## Secret-guard hooks

Three `PreToolUse` hooks keep secrets out of the conversation without blocking normal work:

| Hook | Matcher | Blocks |
| --- | --- | --- |
| `ssh-read-guard.sh` | `Read\|Grep` | Anything under `~/.ssh` except `config`, `known_hosts`, `authorized_keys` and `*.pub` |
| `env-secret-guard.py` | `Read\|Grep\|Write\|Edit` | `.env` files that hold real-looking secrets, and writes of secrets into them. Placeholder-only files such as `.env.example` stay readable |
| `bash-secret-guard.py` | `Bash` | Shell commands that would print or copy those files. Commands that only *use* them are allowed (`ssh -i`, `docker --env-file`). It asks before `cp`/`mv`/`rm`/`git` on a secret-holding `.env` |

Each hook has `"onFailure": "block"`:

```json
{
  "type": "command",
  "command": "~/.claude/hooks/bash-secret-guard.py",
  "timeout": 10,
  "onFailure": "block"
}
```

By default a `PreToolUse` hook that crashes, times out or prints invalid JSON **fails open**, so the tool call goes ahead. With `onFailure: "block"` a broken guard blocks the call instead. If every Read or Bash call starts failing with a hook error, check that the hook's interpreter still works (for example `python3` after a Python upgrade) before suspecting Claude Code.

!!! info "Why not `permissions.deny`?"
    Deny rules are the usual hard block for secret files, but they match paths only. `Read(**/.env*)` would also block placeholder files and `.env` files with no secrets, and `Read(~/.ssh/**)` would block `config` and `known_hosts`. The hooks look at file contents, so they can make that distinction.

To test a hook outside a session, pipe it the JSON that Claude Code sends:

```bash
echo '{"tool_name":"Read","tool_input":{"file_path":"~/.ssh/id_ed25519"}}' \
  | ~/.claude/hooks/ssh-read-guard.sh          # prints a "deny" decision
echo '{"tool_name":"Bash","tool_input":{"command":"ls"}}' \
  | ~/.claude/hooks/bash-secret-guard.py; echo $?   # no output, exit 0 = allowed
```

## Custom subagents

A subagent's `tools:` frontmatter is an allowlist, and MCP tools must be listed by their full name. The Context7 docs agent, for example, needs the names of the claude.ai connector tools:

```yaml
tools: mcp__claude_ai_Context7__resolve-library-id, mcp__claude_ai_Context7__query-docs, Read, Grep, Glob
```

When a connector or plugin renames its tools, agents that list the old names lose access without any error.

## Health checks

Run these after a Claude Code update, or when something seems off:

```bash
claude doctor                          # install method, version, auto-updates, search
claude mcp list                        # connection status of every MCP server
claude plugin list                     # enabled/disabled plugins and load errors
claude plugin validate ~/.claude       # skills, agents and commands
claude plugin validate ~/.agents/skills  # skills symlinked into ~/.claude/skills (validate doesn't follow symlinks)
claude auto-mode config                # the auto mode rules actually in effect
```

Inside a session, `/doctor` runs a fuller check that can also fix problems, and `/hooks` lists the hooks that are loaded.
