# Contributing

Use Python 3.14+ and a locally built `toolframe-sdk==0.4.0+web.1` wheel. Install
this package and its development dependencies with that wheelhouse:

```sh
uv sync --no-sources --group dev --find-links /path/to/wheelhouse
uv run --no-sync pytest -q
uv run --no-sync ruff check src tests scripts
uv run --no-sync ruff format --check src tests scripts
uv build --wheel
```

Package tests cover preparation, sealed assets, HTTP handling and lifecycle
boundaries. Installed acceptance additionally exercises the matching Core and
provider wheels through the public application URL, authentication, browser
forms, mount changes, update failures and retained business data. Neither test
layer substitutes for the other. Platform/compiler and external-authentication
combinations require their own execution evidence.

Keep generated build/test output in `build/` or `dist/`, local reports in `.local/`,
and issue working state in `.issues/`. Business fixtures belong in tests; public
examples must use public SDK/provider interfaces.

Install `prek==0.5.5` and run `prek install --allow-missing-config`. The repository
hooks check branch names, Ruff lint/format, and Conventional Commit messages.
`prek run --all-files` uses the same pinned Ruff configuration. Work branches use
`feat/`, `fix/`, `release/` or `chore/` plus a lowercase hyphenated description;
release versions may contain dots. See [cchk.toml](cchk.toml).

Keep this repository independently installable. Do not substitute editable sibling
sources for wheel-combination verification. Remote publication is a separate
release action with published dependency versions and platform verification.
