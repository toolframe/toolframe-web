# Releasing Toolframe Web

The distribution is `toolframe-web`; `pyproject.toml` defines its version.
Publish SDK 0.4.1 before Web 0.1.0. Core 0.8 consumes the Web authoring manifest
and provides the runtime gateway.

Run [CI validation](ci.md) and the affected installed-application and browser
checks from [Contributing](../CONTRIBUTING.md). Commit the verified sources,
merge through a passing PR, and tag that main commit as `v<project.version>`.

`publish.yml` is manually dispatched on the matching version tag. Select `pypi`
for production or `testpypi` when its publisher is configured. The workflow
reruns Linux/macOS validation and uploads only the distributions from that run;
its publishing job never rebuilds them. Published files cannot be replaced.

Configure a PyPI pending or existing Trusted Publisher with:

| Field | Value |
| --- | --- |
| Project | `toolframe-web` |
| GitHub owner | `toolframe` |
| Repository | `toolframe-web` |
| Workflow | `publish.yml` |
| Environment | `pypi` |

The GitHub `pypi` environment permits version tags (`v*`). A TestPyPI publisher
uses the same repository and workflow with a separate `testpypi` environment.
Only the publishing job receives `id-token: write`; no API token is stored.
Repository visibility does not control package visibility: PyPI artifacts are
public, including the source distribution.

After publication, verify public-index installation in a new environment:

```sh
uv venv --python 3.14.7 build/consumer
uv pip install --python build/consumer/bin/python 'toolframe-web[authoring]==0.1.0'
uv pip check --python build/consumer/bin/python
```

Record exact revisions, artifact hashes and installed-application evidence in CI
artifacts or ignored local verification directories. TestPyPI and local-wheel
checks alone do not establish production PyPI resolution.
