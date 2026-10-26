# Build and run Web Notes

Use Python 3.14+, Core 0.8 and SDK 0.4.1. Discover and generate the Notes
template from its package. For a new application, run the commands below. If
you already generated Notes from the README, initialize `APP` if needed and
continue with the provider installation; do not generate the same plugin twice.

```sh
toolframe --home APP init
toolframe --home APP template show toolframe-web/notes --from pypi:toolframe-web==0.1.1 --json
toolframe --home APP plugin new notes --from pypi:toolframe-web==0.1.1 --template toolframe-web/notes
toolframe --home APP web prepare APP/plugins/notes
```

Start `toolframe --home APP serve` in another terminal, then install the providers:

```sh
toolframe --home APP plugin add pypi:toolframe-sql-sqlite==0.4.2 --enable
toolframe --home APP plugin add pypi:toolframe-web==0.1.1 --enable
# Install the wheel path printed by prepare:
toolframe --home APP plugin add APP/plugins/notes/build/web/toolframe_notes-0.1.0-py3-none-any.whl --enable
```

Select `schema_version = 2` and explicitly mount `web.notes.main` in the
application's `toolframe.toml`, then apply it:

```toml
[gateway.mounts."web.notes.main"]
prefix = "/"
```

```sh
toolframe --home APP apply
```

The distribution ships a static authoring manifest and owns the full Notes/FSD
template, generator and asset preparation. `--presentation web` remains an alias
for this optional template. Default `plugin new` creates Core's basic service.
Acquiring this template does not enable or mount the provider. Use an index source
only when its authoring wheel is available, or configure matching local wheels.
