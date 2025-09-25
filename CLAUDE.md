# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This repository contains the MkDocs source for an Arch Linux (CachyOS) setup guide documentation site. The site is built using MkDocs with the Material theme and deploys to <https://archconfig.tahmidul612.com>.

## Development Commands

### Setup Virtual Environment

```bash
python -m venv .venv
source .venv/bin/activate
pip install mkdocs mkdocs-material[imaging]
pip install mkdocs-callouts mkdocs-include-markdown-plugin mkdocs-replace-markdown
```

### Run Development Server

```bash
mkdocs serve
```

The site will be available at <http://localhost:8000> with auto-reload on file changes.

### Build Static Site

```bash
mkdocs build
```

Generates the static site in the `site/` directory.

## Project Structure

- `mkdocs.yml` - Main configuration file for MkDocs site settings, theme configuration, and plugins
- `docs/` - Contains all documentation markdown files
  - `index.md` - Home page
  - `CachyOS/` - CachyOS-specific documentation
  - `assets/` - Images and icons (arch-linux.svg, cog-custom.png)
  - `stylesheets/extra.css` - Custom CSS overrides
- Documentation uses MkDocs Material theme with dark/light mode toggle

## Key Configuration

The site uses several MkDocs plugins and extensions:

- **Plugins**: callouts, include-markdown, search, social
- **Markdown Extensions**: admonition, pymdownx (details, highlight, keys, superfences, tabbed), table of contents with permalinks
- **Theme Features**: Code copy button, navigation expansion, instant loading with progress, search highlighting and suggestions

## Documentation Guidelines

When adding new documentation:

1. Place markdown files in appropriate subdirectories under `docs/`
2. Navigation structure is automatically generated from the directory structure
3. Use admonitions for tips, warnings, and notes
4. Code blocks support syntax highlighting and copy buttons
5. Tabbed content can be created using pymdownx.tabbed extension
