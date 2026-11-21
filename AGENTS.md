# Agent Instructions

MkDocs (Material) source for a public Arch Linux / CachyOS setup guide, plus the scripts and dotfiles it documents. Published at <https://archconfig.tahmidul612.com>.

## Package Manager

- Use **uv**: `uv sync`. Add or remove dependencies with `uv add` / `uv remove`; never `pip` or `requirements.txt`.

## Commands

| Task | Command |
| --- | --- |
| Preview with reload | `uv run mkdocs serve` (<http://localhost:8000>) |
| Check the site | `uv run mkdocs build --strict` (must finish with no warnings) |
| Shell scripts | `uvx --from shellcheck-py shellcheck path/to/script.sh` |
| Python scripts | `python -I -m py_compile path/to/script.py` |

## Structure

| Path | Contents |
| --- | --- |
| `docs/` | Site pages; navigation follows the folder tree |
| `mkdocs.yml` | Theme, plugins, markdown extensions |
| `main.py` | mkdocs-macros functions (`content_tabs`) |
| `dotfiles/` | Configs plus their install scripts |
| `tools/<name>/` | Standalone tools (e.g. `tools/resmon/`, `tools/win11-vm/`) |

## Docs Conventions

- Each folder's landing page is `README.md`; never put an `index.md` next to it.
- Use admonitions (`!!! note`) for tips and warnings, `=== "Tab"` blocks for host/guest or alternative variants.
- The macros plugin renders pages as Jinja: no literal `{{` or `{%` in docs.
- `nl2br` is on: don't hard-wrap prose, every newline becomes a line break.
- Link to repo files with GitHub blob URLs (`https://github.com/tahmidul612/arch-config/blob/main/...`); style reference: `docs/Utilities/tmux.md`.
- Each setup step says what it does, why, the command, and how to check it.

## tools/ Convention

- One flat folder per tool with a `README.md` holding a table: file, installs to, setup, status.
- No secrets in the repo: no keys, certificates, passwords or tokens. Show how to generate them instead.
- Use placeholders (`<USER>`, `<HOST>`) or `$SUDO_USER`, `$XDG_RUNTIME_DIR`, env variables for anything machine-specific.

## Privacy (public site)

- Never commit personal data: real names, emails, account or host names, public or LAN IPs (libvirt's `192.168.122.0/24` is fine), MACs, serials, UUIDs, Windows SIDs, home paths.
- Keep private projects and services out of docs; describe the pattern, not the instance.
- Grep staged files for these before every commit.

## Git

- Conventional commits: `type(scope): summary`, e.g. `docs(gaming): ...`, `feat(resmon): ...`.
- Stage explicit paths; other sessions may share the working tree.
- AI commits MUST end with the agent's `Co-Authored-By:` attribution line (and session link if one is provided).
