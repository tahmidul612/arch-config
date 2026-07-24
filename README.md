# Arch Linux (CachyOS) Setup

This repo hosts the mkdocs source files for my arch linux setup guide site

## MkDocs Setup

Dependencies are managed with [uv](https://docs.astral.sh/uv/). Install it first, then:

- Create the virtual environment and install everything from `uv.lock`

    ```shell
    uv sync
    ```

- Serve the site locally at <http://localhost:8000>

    ```shell
    uv run mkdocs serve
    ```

- Build the static site into `site/`

    ```shell
    uv run mkdocs build
    ```

To add a dependency, use `uv add <package>` so `pyproject.toml` and `uv.lock` stay in sync.
