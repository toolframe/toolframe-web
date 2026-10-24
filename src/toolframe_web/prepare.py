"""Author-time capture of explicit browser files and a pinned Tailwind recipe."""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import platform
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from pydantic import Field, model_validator
from toolframe_sdk import digest
from toolframe_sdk.contracts import Contract
from toolframe_sdk.web import relative_file

from .assets import MAX_FILE, AssetFile, Document, media_type, safe_file, seal

TAILWIND_VERSION = "4.3.3"
TAILWIND = {
    ("Darwin", "arm64"): (
        "macos-arm64",
        "cdf646702987a743464dff4d9c60fd4480d1c1e73dd819a9a67f1078815dce9d",
    ),
    ("Darwin", "x86_64"): (
        "macos-x64",
        "7922e0953f2110c05976e3bf58f14e643d90427575e766b7d433f5f80cbee7e1",
    ),
    ("Linux", "aarch64"): (
        "linux-arm64",
        "55fd0b241214eff3de1e8ee4f22796662f2d2e7a49bcfca7477cfd0bac398195",
    ),
    ("Linux", "x86_64"): (
        "linux-x64",
        "dc61b3ac6b8c9ca874c0cc4c57b2409791a64c5540404ca5f5367360babc313a",
    ),
}
EXCLUDED = {
    ".git",
    ".venv",
    ".local",
    ".issues",
    ".worktrees",
    "node_modules",
    "build",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
}


class FileInput(Contract):
    path: str
    source: str | None = None
    url: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    media_type: str | None = None
    license: str | None = None
    dependencies: tuple[str, ...] = ()

    @model_validator(mode="after")
    def explicit(self):
        relative_file(self.path)
        if bool(self.source) == bool(self.url):
            raise ValueError("Declare one local source or pinned HTTPS URL")
        if self.source:
            relative_file(self.source)
        if self.url and (
            not self.sha256
            or "latest" in self.url.lower()
            or "/main/" in self.url
            or "/master/" in self.url
        ):
            raise ValueError("Remote files require a versioned URL and SHA-256")
        if self.media_type and self.media_type != media_type(self.path):
            raise ValueError("Asset MIME type does not match extension")
        return self


class ApplicationInput(Contract):
    resource: str
    package: str
    manifest_output: str = "web/assets.json"
    public_output: str = "web/public"

    @model_validator(mode="after")
    def locations(self):
        if not re.fullmatch(r"[a-zA-Z]\w*(?:\.\w+)*", self.package):
            raise ValueError("Application package must be an import package")
        relative_file(self.manifest_output)
        relative_file(self.public_output)
        if (
            not self.manifest_output.startswith("web/")
            or not self.manifest_output.endswith(".json")
            or not self.public_output.startswith("web/")
        ):
            raise ValueError(
                "Generated Web outputs must live in the package's web directory"
            )
        return self


class TailwindInput(Contract):
    version: str = TAILWIND_VERSION
    input: str
    sources: tuple[str, ...]
    output: str = "app.css"
    license: str = "licenses/tailwind.txt"

    @model_validator(mode="after")
    def selected(self):
        if self.version != TAILWIND_VERSION:
            raise ValueError(
                f"The supported standalone Tailwind recipe is {TAILWIND_VERSION}"
            )
        for value in (self.input, self.output, self.license, *self.sources):
            relative_file(value)
        if (
            not self.sources
            or len(self.sources) > 512
            or not self.output.endswith(".css")
        ):
            raise ValueError(
                "Tailwind requires 1–512 explicit source files and a CSS output"
            )
        return self


class SourceManifest(Contract):
    schema_version: int = Field(ge=1, le=1)
    application: ApplicationInput
    document: Document = Field(default_factory=Document)
    files: tuple[FileInput, ...] = ()
    tailwind: TailwindInput | None = None


def _address(url):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.port not in {None, 443}
    ):
        raise ValueError(
            "Downloads require an HTTPS URL without credentials or a custom port"
        )
    answers = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    addresses = [answer[4][0] for answer in answers]
    if not addresses or any(
        not ipaddress.ip_address(address).is_global for address in addresses
    ):
        raise ValueError("Downloads cannot access local or private networks")
    return parsed, addresses[0]


def fetch(url, checksum, cache, *, offline=False, limit=MAX_FILE):
    target = Path(cache) / "downloads" / checksum
    if target.exists():
        if (
            target.stat().st_size > limit
            or hashlib.sha256(target.read_bytes()).hexdigest() != checksum
        ):
            raise ValueError("Cached download failed integrity verification")
        return target
    if offline:
        raise ValueError("Offline preparation is missing a pinned download")
    target.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 180
    for _ in range(6):
        parsed, address = _address(url)
        # Pin the checked IP on the actual socket. TLS still verifies the original host.
        connection = http.client.HTTPSConnection(parsed.hostname, 443, timeout=30)
        raw = socket.create_connection((address, 443), timeout=30)
        connection.sock = ssl.create_default_context().wrap_socket(
            raw, server_hostname=parsed.hostname
        )
        try:
            connection.request(
                "GET",
                parsed.path + ("?" + parsed.query if parsed.query else ""),
                headers={
                    "User-Agent": "toolframe-web/0.1",
                    "Accept-Encoding": "identity",
                },
            )
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                url = urljoin(url, response.getheader("Location", ""))
                continue
            if response.status != 200:
                raise ValueError(f"Pinned download returned HTTP {response.status}")
            if int(response.getheader("Content-Length", "0")) > limit:
                raise ValueError("Download exceeds the allowed size")
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                temporary = Path(handle.name)
                count, identity = 0, hashlib.sha256()
                try:
                    while data := response.read(65536):
                        count += len(data)
                        if count > limit or time.monotonic() > deadline:
                            raise ValueError("Download exceeded its size or time limit")
                        identity.update(data)
                        handle.write(data)
                    if identity.hexdigest() != checksum:
                        raise ValueError("Pinned download SHA-256 mismatch")
                    handle.flush()
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
            return target
        finally:
            connection.close()
    raise ValueError("Too many download redirects")


def tailwind(source, recipe, cache, *, offline=False):
    platform_key = (platform.system(), platform.machine())
    if platform_key not in TAILWIND or (
        platform_key[0] == "Linux" and platform.libc_ver()[0] != "glibc"
    ):
        raise ValueError(
            "Tailwind standalone supports macOS and glibc Linux on arm64/x64"
        )
    platform_name, checksum = TAILWIND[platform_key]
    binary = fetch(
        f"https://github.com/tailwindlabs/tailwindcss/releases/download/v{TAILWIND_VERSION}/tailwindcss-{platform_name}",
        checksum,
        cache,
        offline=offline,
        limit=128 * 1024 * 1024,
    )
    binary.chmod(0o700)
    css = safe_file(source, recipe.input).read_text()
    imports = re.findall(r"@import\s+([^;]+);", css)
    if (
        len(imports) != 1
        or imports[0].strip()
        not in {'"tailwindcss" source(none)', "'tailwindcss' source(none)"}
        or re.search(r"@(source|plugin|config)\b", css)
    ):
        raise ValueError(
            'Tailwind input must use @import "tailwindcss" source(none); and no @source, @plugin or @config'
        )
    inputs = {}
    for path in recipe.sources:
        file = safe_file(source, path)
        if file.stat().st_size > MAX_FILE:
            raise ValueError("Tailwind scan input exceeds 8 MiB")
        inputs[path] = file.read_bytes()
    identity = digest(
        {
            "version": TAILWIND_VERSION,
            "compiler": checksum,
            "css": css,
            "sources": {
                key: hashlib.sha256(value).hexdigest()
                for key, value in sorted(inputs.items())
            },
        }
    )
    output = Path(cache) / "tailwind" / (identity + ".css")
    report = {
        "tailwind": TAILWIND_VERSION,
        "compiler_sha256": checksum,
        "input_digest": identity,
    }
    if output.exists():
        expected = output.with_suffix(".sha256").read_text()
        if hashlib.sha256(output.read_bytes()).hexdigest() != expected:
            raise ValueError("Cached Tailwind output failed integrity verification")
        return output.read_bytes(), report
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as folder:
        root = Path(folder)
        for path, content in inputs.items():
            target = safe_file(root, "scan/" + path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        entry = root / "input.css"
        entry.write_text(
            css
            + "\n"
            + "\n".join(
                "@source " + json.dumps("./scan/" + path) + ";" for path in inputs
            )
        )
        generated = root / "output.css"
        with tempfile.TemporaryFile() as log:
            result = subprocess.run(
                [
                    str(binary),
                    "--input",
                    str(entry),
                    "--output",
                    str(generated),
                    "--minify",
                ],
                cwd=root,
                env={"PATH": "", "TMPDIR": str(root), "LC_ALL": "C"},
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                timeout=60,
                check=False,
            )
            if result.returncode:
                raise ValueError(
                    "Tailwind compilation failed; check the selected CSS and source files"
                )
        if not generated.is_file() or generated.stat().st_size > MAX_FILE:
            raise ValueError("Tailwind produced no bounded CSS output")
        value = generated.read_bytes()
        output.write_bytes(value)
        output.with_suffix(".sha256").write_text(hashlib.sha256(value).hexdigest())
        return value, report


def stage(source, destination, cache, *, offline=False):
    source, destination = Path(source).resolve(), Path(destination).absolute()
    if destination.exists() or destination.is_relative_to(source):
        raise ValueError("Staging requires a new directory outside the source tree")
    project = tomllib.loads((source / "pyproject.toml").read_text())
    names = (
        project.get("tool", {})
        .get("toolframe", {})
        .get("web", {})
        .get("manifests", ["web-assets.toml"])
    )
    manifests = [
        SourceManifest.model_validate(
            tomllib.loads(safe_file(source, relative_file(name)).read_text())
        )
        for name in names
    ]
    destination.mkdir(parents=True)
    try:
        for root, directories, files in os.walk(source, followlinks=False):
            directories[:] = [name for name in directories if name not in EXCLUDED]
            if any((Path(root) / name).is_symlink() for name in directories):
                raise ValueError("Source capture rejects symlinked directories")
            for name in files:
                relative = (Path(root) / name).relative_to(source).as_posix()
                original = safe_file(source, relative)
                output = destination / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, output)
        result, outputs = [], set()
        for manifest in manifests:
            package = (
                destination / "src" / manifest.application.package.replace(".", "/")
            )
            if not package.is_dir():
                raise ValueError(
                    "Web preparation requires the declared package in a src layout"
                )
            declared, content = [], {}
            recipe = {}
            for item in manifest.files:
                if item.path in content:
                    raise ValueError("Duplicate public asset path")
                file = (
                    fetch(item.url, item.sha256, cache, offline=offline)
                    if item.url
                    else safe_file(source, item.source)
                )
                if file.stat().st_size > MAX_FILE:
                    raise ValueError("Asset exceeds 8 MiB")
                value = file.read_bytes()
                checksum = hashlib.sha256(value).hexdigest()
                if item.sha256 and item.sha256 != checksum:
                    raise ValueError("Local asset SHA-256 mismatch")
                content[item.path] = value
                declared.append(
                    AssetFile(
                        path=item.path,
                        sha256=checksum,
                        size=len(value),
                        media_type=media_type(item.path),
                        source=item.url or item.source,
                        license=item.license,
                        dependencies=item.dependencies,
                    )
                )
            if manifest.tailwind:
                if manifest.tailwind.output in content:
                    raise ValueError("Tailwind output conflicts with a declared asset")
                value, recipe = tailwind(
                    source, manifest.tailwind, cache, offline=offline
                )
                content[manifest.tailwind.output] = value
                declared.append(
                    AssetFile(
                        path=manifest.tailwind.output,
                        sha256=hashlib.sha256(value).hexdigest(),
                        size=len(value),
                        media_type="text/css",
                        source="tailwind:" + TAILWIND_VERSION,
                        license=manifest.tailwind.license,
                    )
                )
            sealed = seal(
                manifest.application.public_output,
                declared,
                document=manifest.document,
                recipe=recipe,
            )
            for item in sealed.files:
                path = safe_file(package, sealed.public + "/" + item.path)
                if path in outputs:
                    raise ValueError(
                        "Web manifests cannot overwrite each other's outputs"
                    )
                outputs.add(path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content[item.path])
            path = safe_file(package, manifest.application.manifest_output)
            if path in outputs:
                raise ValueError("Duplicate generated manifest output")
            outputs.add(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(sealed.model_dump_json(indent=2) + "\n")
            result.append(
                {
                    "resource": manifest.application.resource,
                    "digest": sealed.digest,
                    "manifest": str(path.relative_to(destination)),
                    "files": len(sealed.files),
                }
            )
        return {"staging": str(destination), "applications": result}
    except BaseException:
        shutil.rmtree(destination)
        raise
