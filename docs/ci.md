# Continuous integration

`ci.yml` validates Python 3.14.7 distributions on Ubuntu 24.04 and macOS 15 with
uv 0.12.8. It builds a wheel from the sdist, checks metadata and every declared
license, runs Ruff and package tests against the installed wheel, checks strict
Twine metadata, and installs wheel and sdist in separate clean environments.
All dependencies resolve from PyPI; no sibling repository token is required.

`quality.yml` checks branch naming, Ruff lint and formatting, and Conventional
Commit messages and PR titles. Third-party Actions are pinned to commit IDs.
Dependabot groups Action version and security updates into compliant branches.

Run the same validation locally:

```sh
python3.14 scripts/validate_release.py --output build/release-dist
```

Use a new output directory for each run. Pass `--wheelhouse PATH` for explicit
prepublication dependency wheels. CI retains only successfully validated
wheel/sdist artifacts for 14 days. Browser qualification and installed Core
application acceptance remain separate checks; see [Contributing](../CONTRIBUTING.md).
