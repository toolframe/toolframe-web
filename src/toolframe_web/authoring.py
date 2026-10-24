"""Distribution authoring entries, independent of runtime plugin activation."""

from importlib.metadata import version
from importlib.resources import files

import tomlkit
from toolframe_sdk.authoring import render_plugin, write_template


def generate(*, destination, parameters):
    rendered = render_plugin(
        files("toolframe_web").joinpath("templates/notes"),
        parameters["name"],
        text_suffixes=(".toml", ".html", ".js", ".md"),
    )
    from pathlib import Path

    project = tomlkit.parse(rendered[Path("pyproject.toml")].decode())
    requirement = "toolframe-web==" + version("toolframe-web")
    project["project"]["optional-dependencies"]["web"] = [requirement]
    project["tool"]["toolframe"] = {"authoring": {"source": "pypi:" + requirement}}
    rendered[Path("pyproject.toml")] = tomlkit.dumps(project).encode()
    write_template(destination, rendered)


def prepare(*, source, destination, cache, offline):
    from .prepare import stage

    return stage(source, destination, cache, offline=offline)
