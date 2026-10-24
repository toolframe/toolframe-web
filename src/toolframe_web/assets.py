"""Sealed, allowlisted browser asset sets. Runtime never downloads or builds."""

from __future__ import annotations

import hashlib
import importlib.resources
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from toolframe_sdk import PluginError, digest
from toolframe_sdk.contracts import Contract
from toolframe_sdk.web import relative_file

MEDIA_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".html": "text/html",
    ".json": "application/json",
    ".map": "application/json",
    ".wasm": "application/wasm",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".txt": "text/plain",
}
MAX_FILE = 8 * 1024 * 1024
MAX_SET = 32 * 1024 * 1024


def media_type(path):
    try:
        return MEDIA_TYPES[Path(path).suffix.lower()]
    except KeyError:
        raise ValueError(f"Unsupported public asset extension: {path}") from None


class AssetFile(Contract):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=0, le=MAX_FILE)
    media_type: str
    source: str = "local"
    license: str | None = None
    dependencies: tuple[str, ...] = ()

    @model_validator(mode="after")
    def paths(self):
        relative_file(self.path)
        if self.media_type != media_type(self.path):
            raise ValueError("Asset MIME type must match its declared file extension")
        for value in self.dependencies:
            relative_file(value)
        if self.license:
            relative_file(self.license)
        return self


class Document(Contract):
    styles: tuple[str, ...] = ()
    scripts: tuple[str, ...] = ()
    modules: tuple[str, ...] = ()
    imports: dict[str, str] = Field(default_factory=dict)
    visibility: str = "inherit"

    @field_validator("visibility")
    @classmethod
    def visibility_value(cls, value):
        if value not in {"inherit", "public"}:
            raise ValueError("Asset visibility must be inherit or public")
        return value


class Manifest(Contract):
    schema_version: int = Field(default=1, ge=1, le=1)
    public: str = "web/public"
    document: Document = Field(default_factory=Document)
    files: tuple[AssetFile, ...]
    recipe: dict = Field(default_factory=dict)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def sealed(self):
        relative_file(self.public)
        paths = {item.path for item in self.files}
        if (
            len(self.files) > 2048
            or len(paths) != len(self.files)
            or len({path.casefold() for path in paths}) != len(paths)
        ):
            raise ValueError("Asset paths must be distinct and bounded")
        if sum(item.size for item in self.files) > MAX_SET:
            raise ValueError("Asset set exceeds 32 MiB")
        referenced = {
            *self.document.styles,
            *self.document.scripts,
            *self.document.modules,
            *self.document.imports.values(),
        }
        referenced |= {path for item in self.files for path in item.dependencies}
        referenced |= {item.license for item in self.files if item.license}
        if not referenced <= paths:
            raise ValueError("Manifest references an undeclared asset or license")
        if any(media_type(path) != "text/css" for path in self.document.styles):
            raise ValueError("Style entries must be CSS")
        if any(
            media_type(path) != "text/javascript"
            for path in (
                *self.document.scripts,
                *self.document.modules,
                *self.document.imports.values(),
            )
        ):
            raise ValueError("Script and import entries must be JavaScript")
        if self.digest != digest(self.model_dump(mode="json", exclude={"digest"})):
            raise ValueError("Asset manifest digest mismatch")
        return self


def seal(public, files, *, document=None, recipe=None):
    payload = {
        "schema_version": 1,
        "public": public,
        "document": (document or Document()).model_dump(mode="json"),
        "files": [
            item.model_dump(mode="json")
            for item in sorted(files, key=lambda item: item.path)
        ],
        "recipe": recipe or {},
    }
    return Manifest(**payload, digest=digest(payload))


def safe_file(root, relative):
    relative_file(relative)
    root = Path(root).resolve()
    path = root.joinpath(relative)
    if not path.resolve().is_relative_to(root) or any(
        parent.is_symlink()
        for parent in (path, *path.parents)
        if parent != root and parent.is_relative_to(root)
    ):
        raise ValueError("Assets cannot contain symlinks or escape their package")
    return path


@dataclass(frozen=True)
class AssetSet:
    manifest: Manifest
    content: dict

    @classmethod
    def load(cls, package, manifest_path):
        root = Path(str(importlib.resources.files(package)))
        path = safe_file(root, manifest_path)
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("Asset manifest exceeds 1 MiB")
        manifest = Manifest.model_validate_json(path.read_bytes())
        content = {}
        for item in manifest.files:
            file = safe_file(root, manifest.public + "/" + item.path)
            if file.stat().st_size != item.size:
                raise ValueError("Asset size mismatch")
            value = file.read_bytes()
            if hashlib.sha256(value).hexdigest() != item.sha256:
                raise ValueError("Asset content hash mismatch")
            content[item.path] = value
        return cls(manifest, content)

    def persist(self, root):
        target = Path(root) / self.manifest.digest
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        for item in self.manifest.files:
            path = safe_file(target, "public/" + item.path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.content[item.path])
        (target / "manifest.json").write_text(self.manifest.model_dump_json())

    @classmethod
    def cached(cls, root, checksum):
        if len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
            raise PluginError("web_asset_missing", "Unknown Web asset set")
        target = Path(root) / checksum
        manifest = Manifest.model_validate_json((target / "manifest.json").read_bytes())
        if manifest.digest != checksum:
            raise ValueError("Cached asset identity mismatch")
        content = {}
        for item in manifest.files:
            path = safe_file(target, "public/" + item.path)
            if path.stat().st_size != item.size:
                raise ValueError("Cached asset size mismatch")
            value = path.read_bytes()
            if hashlib.sha256(value).hexdigest() != item.sha256:
                raise ValueError("Cached asset hash mismatch")
            content[item.path] = value
        return cls(manifest, content)
