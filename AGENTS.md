# Agent Instructions

MkDocs (Material) source for a public Arch Linux / CachyOS setup guide, plus the scripts and dotfiles it documents. Published at <https://archconfig.tahmidul612.com>.

## Package Manager

- Use **uv** only: `uv sync`, and `uv add` / `uv remove` so `pyproject.toml` and `uv.lock` stay in sync.

## Commands

| Task | Command |
| --- | --- |
| Preview with reload | `uv run mkdocs serve` (<http://localhost:8000>) |
| Check the site | `uv run mkdocs build --strict` (must end with zero warnings) |
| Shell scripts | `uvx --from shellcheck-py shellcheck path/to/script.sh` |
| Python scripts | `python -I -m py_compile path/to/script.py` |

## Docs Conventions

- Navigation follows the `docs/` folder tree; each folder's landing page is `README.md` (the only landing file in that folder).
- Use admonitions (`!!! note`) for tips and warnings, `=== "Tab"` blocks for host/guest or alternative variants.
- The macros plugin (`main.py`) renders every page as Jinja: write `{{` and `{%` only inside a `{% raw %}` block.
- `nl2br` is on: write each paragraph as one line.
- Link repo files with GitHub blob URLs (`https://github.com/tahmidul612/arch-config/blob/main/...`); style reference: `docs/Utilities/tmux.md`.
- Each setup step gives what it does, why, the command, and how to check it.

## tools/ Convention

- One folder per tool (`tools/<name>/`) with a `README.md` table: file, installs to, setup, status.
- Ship generators for keys, certificates and passwords; the files themselves stay out of the repo.
- Machine-specific values become placeholders (`<USER>`, `<HOST>`) or come from `$SUDO_USER`, `$XDG_RUNTIME_DIR` and env variables.

## Privacy (public site)

- Committed text is public: use placeholders for real names, emails, account and host names, public or LAN IPs (libvirt's `192.168.122.0/24` is fine), MACs, serials, UUIDs, Windows SIDs and home paths.
- Describe private projects and services as a pattern, unnamed.
- Grep staged files for these before every commit.

## Git

- Conventional commits: `type(scope): summary`, e.g. `docs(gaming): ...`, `feat(resmon): ...`.
- Stage explicit paths; other sessions may share the working tree.
- AI commits MUST end with the agent's `Co-Authored-By:` attribution line (and session link if one is provided).
