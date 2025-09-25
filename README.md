# Arch Linux (CachyOS) Setup

This repo hosts the mkdocs source files for my arch linux setup guide site

## MkDocs Setup

Steps to install MkDocs with Material theme and all other needed plugins.

- Create python venv

    ```shell
    python -m venv .venv
    ```

- Install mkdocs and material theme

    ```shell
    pip install mkdocs mkdocs-material
    ```

- Install plugins

    ```shell
    pip install mkdocs-callouts mkdocs-include-markdown-plugin mkdocs-replace-markdown
    ```
