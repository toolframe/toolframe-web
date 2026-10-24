"""Bounded request decoding after Core has authorized the selected route."""

from __future__ import annotations

import re
from types import MappingProxyType
from urllib.parse import parse_qsl

from starlette.datastructures import FormData, UploadFile
from toolframe_sdk import PluginError, WebUpload

MAX_FIELDS = 128
MAX_FILES = 8
MAX_FIELD_BYTES = 64 * 1024
MAX_NAME_BYTES = 256
MAX_UPLOAD_BYTES = 4 * 1024 * 1024
FORM_TYPES = {"application/x-www-form-urlencoded", "multipart/form-data"}


class RequestBody:
    def __init__(self, request, limit):
        self.request = request
        self.limit = limit
        self.content = b""
        self.parsed = None
        self.values = None
        self.media_type = (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        )

    async def read(self):
        body = bytearray()
        async for chunk in self.request.stream():
            if len(body) + len(chunk) > self.limit:
                raise PluginError("request_too_large", "Request exceeds the body limit")
            body.extend(chunk)
        self.content = bytes(body)
        self.request._body = self.content

    async def form(self):
        if self.values is not None:
            return self.values
        if self.media_type == "application/x-www-form-urlencoded":
            try:
                encoded = self.content.decode("utf-8")
                if re.search(r"%(?![0-9a-fA-F]{2})", encoded):
                    raise ValueError("Invalid percent encoding")
                self.parsed = FormData(
                    parse_qsl(
                        encoded,
                        keep_blank_values=True,
                        max_num_fields=MAX_FIELDS,
                        encoding="utf-8",
                        errors="strict",
                    )
                )
            except UnicodeError, ValueError:
                raise PluginError(
                    "invalid_request", "Invalid form encoding or field limits"
                ) from None
        else:
            self.parsed = await self.request.form(
                max_files=MAX_FILES,
                max_fields=MAX_FIELDS,
                max_part_size=min(self.limit, MAX_FIELD_BYTES),
            )
        values = {}
        for name, value in self.parsed.multi_items():
            if len(name.encode("utf-8")) > MAX_NAME_BYTES:
                raise PluginError(
                    "invalid_request", "Form field name exceeds 256 bytes"
                )
            if name in values:
                raise PluginError(
                    "invalid_request",
                    "Duplicate form fields are not supported; use JSON arrays",
                )
            if isinstance(value, UploadFile):
                content = await value.read(MAX_UPLOAD_BYTES + 1)
                if len(content) > MAX_UPLOAD_BYTES:
                    raise PluginError("invalid_request", "Uploaded file exceeds 4 MiB")
                value = WebUpload(
                    value.filename or "",
                    value.content_type or "application/octet-stream",
                    content,
                )
            elif len(value.encode("utf-8")) > MAX_FIELD_BYTES:
                raise PluginError("invalid_request", "Form field value exceeds 64 KiB")
            values[name] = value
        self.values = MappingProxyType(values)
        return self.values

    async def close(self):
        if self.parsed is not None:
            await self.parsed.close()
