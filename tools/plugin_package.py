#!/usr/bin/env python3
"""Pack and validate WebInk v1 plugin data without executing its source."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any
from urllib.parse import urlsplit

PACKAGE_LIMIT = 512 * 1024
SOURCE_LIMIT = 256 * 1024
MANIFEST_LIMIT = 16 * 1024
LICENSE_LIMIT = 64 * 1024
MAX_DEPTH = 12
MAX_OBJECT_MEMBERS = 100
MAX_ARRAY_ITEMS = 100
HOST_VERSION = (0, 9, 0)

PACKAGE_FIELDS = {"packageVersion", "manifest", "source", "licenseText"}
MANIFEST_FIELDS = {
    "manifestVersion", "id", "version", "name", "description", "author",
    "repository", "license", "minHostVersion", "apiVersion", "commands",
    "permissions", "networkOrigins",
}
COMMAND_FIELDS = {"id", "title"}
LICENSES = {"Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause"}
PERMISSIONS = {"page.info", "selection.read", "storage", "network", "page.style"}
SPECIAL_KEYS = {"__proto__", "prototype", "constructor"}
ID_RE = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)+\Z")
COMMAND_ID_RE = re.compile(r"[a-z][a-z0-9-]{0,39}\Z")
SEMVER_RE = re.compile(r"(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})\Z")
REPOSITORY_RE = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
HOST_RE = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z")


class PackageError(ValueError):
    """The input does not satisfy package format v1."""


def _reject_constant(value: str) -> None:
    raise PackageError(f"invalid JSON number: {value}")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PackageError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _check_json_string(value: str) -> None:
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
        raise PackageError("JSON contains a lone surrogate")


def _walk_json(value: Any, depth: int = 0) -> None:
    if isinstance(value, str):
        _check_json_string(value)
    elif isinstance(value, list):
        container_depth = depth + 1
        if container_depth > MAX_DEPTH:
            raise PackageError(f"JSON depth exceeds {MAX_DEPTH}")
        if len(value) > MAX_ARRAY_ITEMS:
            raise PackageError(f"JSON array exceeds {MAX_ARRAY_ITEMS} items")
        for item in value:
            _walk_json(item, container_depth)
    elif isinstance(value, dict):
        container_depth = depth + 1
        if container_depth > MAX_DEPTH:
            raise PackageError(f"JSON depth exceeds {MAX_DEPTH}")
        if len(value) > MAX_OBJECT_MEMBERS:
            raise PackageError(f"JSON object exceeds {MAX_OBJECT_MEMBERS} members")
        for key, item in value.items():
            _check_json_string(key)
            if key in SPECIAL_KEYS:
                raise PackageError(f"prototype-special JSON key is forbidden: {key}")
            _walk_json(item, container_depth)


def _load_json(data: bytes, limit: int, label: str) -> Any:
    if len(data) > limit:
        raise PackageError(f"{label} exceeds {limit} UTF-8 bytes")
    try:
        text = data.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise PackageError(f"{label} is not strict UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_constant=_reject_constant,
            parse_float=lambda value: (_ for _ in ()).throw(PackageError(f"non-integer JSON number: {value}")),
        )
    except json.JSONDecodeError as exc:
        raise PackageError(f"invalid {label} JSON: {exc.msg}") from exc
    except (RecursionError, ValueError) as exc:
        if isinstance(exc, PackageError):
            raise
        raise PackageError(f"invalid {label} JSON") from exc
    _walk_json(value)
    return value


def canonical_json(value: Any) -> bytes:
    """Return the cross-client canonical UTF-8 representation (without newline)."""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (UnicodeEncodeError, ValueError) as exc:
        raise PackageError("value cannot be canonicalized as strict JSON") from exc


def _object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PackageError(f"{label} must be an object")
    unknown = set(value) - fields
    missing = fields - set(value)
    if unknown:
        raise PackageError(f"unknown {label} field: {sorted(unknown)[0]}")
    if missing:
        raise PackageError(f"missing {label} field: {sorted(missing)[0]}")
    return value


def _string(value: Any, label: str, maximum: int, *, nonempty: bool = True) -> str:
    if not isinstance(value, str):
        raise PackageError(f"{label} must be a string")
    if (nonempty and not value) or len(value) > maximum:
        raise PackageError(f"{label} must contain {1 if nonempty else 0}..{maximum} Unicode code points")
    return value


def _exact_int(value: Any, expected: int, label: str) -> None:
    if type(value) is not int or value != expected:
        raise PackageError(f"{label} must be integer {expected}")


def _semver(value: Any, label: str) -> tuple[int, int, int]:
    text = _string(value, label, 17)
    match = SEMVER_RE.fullmatch(text)
    if not match:
        raise PackageError(f"{label} must be stable major.minor.patch")
    parts = tuple(int(part) for part in match.groups())
    if any(part > 65535 for part in parts):
        raise PackageError(f"{label} component exceeds 65535")
    return parts  # type: ignore[return-value]


def _unique_strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PackageError(f"{label} must be an array of strings")
    if len(set(value)) != len(value):
        raise PackageError(f"{label} contains a duplicate")
    return value


def _validate_origin(origin: str) -> None:
    if len(origin) > 2048:
        raise PackageError("network origin exceeds 2048 Unicode code points")
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError as exc:
        raise PackageError("invalid network origin") from exc
    host = parsed.hostname
    if parsed.scheme != "https" or not host or parsed.username is not None or parsed.password is not None:
        raise PackageError("network origin must be canonical HTTPS without userinfo")
    if port is not None or parsed.path or parsed.query or parsed.fragment:
        raise PackageError("network origin must be exactly https://host using default port 443")
    if origin != f"https://{host}" or not HOST_RE.fullmatch(host) or ".." in host or "." not in host:
        raise PackageError("network origin hostname is not canonical")
    labels = host.split(".")
    if any(not label or len(label) > 63 or label[0] == "-" or label[-1] == "-" for label in labels):
        raise PackageError("invalid network origin hostname")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise PackageError("IP literal network origins are forbidden")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise PackageError("local network origins are forbidden")


def validate_manifest(value: Any) -> dict[str, Any]:
    manifest = _object(value, MANIFEST_FIELDS, "manifest")
    _exact_int(manifest["manifestVersion"], 1, "manifestVersion")
    plugin_id = _string(manifest["id"], "id", 80)
    if len(plugin_id) < 3 or not ID_RE.fullmatch(plugin_id):
        raise PackageError("id must be a 3..80 character namespaced lowercase ID")
    _semver(manifest["version"], "version")
    _string(manifest["name"], "name", 80)
    _string(manifest["description"], "description", 500)
    _string(manifest["author"], "author", 100)
    repository = _string(manifest["repository"], "repository", 2048)
    if not REPOSITORY_RE.fullmatch(repository):
        raise PackageError("repository must be a canonical HTTPS GitHub owner/repository URL")
    license_id = _string(manifest["license"], "license", 32)
    if license_id not in LICENSES:
        raise PackageError("license is not in the v1 SPDX allowlist")
    _semver(manifest["minHostVersion"], "minHostVersion")
    if _semver(manifest["minHostVersion"], "minHostVersion") > HOST_VERSION:
        raise PackageError("minHostVersion exceeds host format target 0.9.0")
    _exact_int(manifest["apiVersion"], 1, "apiVersion")

    commands = manifest["commands"]
    if not isinstance(commands, list) or not 1 <= len(commands) <= 8:
        raise PackageError("commands must contain 1..8 entries")
    command_ids: set[str] = set()
    for command_value in commands:
        command = _object(command_value, COMMAND_FIELDS, "command")
        command_id = _string(command["id"], "command id", 40)
        if not COMMAND_ID_RE.fullmatch(command_id):
            raise PackageError("invalid command id")
        if command_id in command_ids:
            raise PackageError("duplicate command id")
        command_ids.add(command_id)
        _string(command["title"], "command title", 80)

    permissions = _unique_strings(manifest["permissions"], "permissions")
    if any(permission not in PERMISSIONS for permission in permissions):
        raise PackageError("unknown permission")
    origins = _unique_strings(manifest["networkOrigins"], "networkOrigins")
    if "network" in permissions:
        if not 1 <= len(origins) <= 4:
            raise PackageError("network permission requires 1..4 origins")
    elif origins:
        raise PackageError("networkOrigins requires network permission")
    for origin in origins:
        _validate_origin(origin)

    encoded = canonical_json(manifest)
    if len(encoded) > MANIFEST_LIMIT:
        raise PackageError(f"manifest exceeds {MANIFEST_LIMIT} canonical UTF-8 bytes")
    return manifest


def validate_package_bytes(data: bytes) -> dict[str, Any]:
    package = _object(_load_json(data, PACKAGE_LIMIT, "package"), PACKAGE_FIELDS, "package")
    _exact_int(package["packageVersion"], 1, "packageVersion")
    validate_manifest(package["manifest"])
    source = _string(package["source"], "source", SOURCE_LIMIT)
    try:
        source_bytes = source.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise PackageError("source contains a lone surrogate") from exc
    if len(source_bytes) > SOURCE_LIMIT:
        raise PackageError(f"source exceeds {SOURCE_LIMIT} UTF-8 bytes")
    license_text = _string(package["licenseText"], "licenseText", LICENSE_LIMIT)
    try:
        license_bytes = license_text.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise PackageError("licenseText contains a lone surrogate") from exc
    if len(license_bytes) > LICENSE_LIMIT:
        raise PackageError(f"licenseText exceeds {LICENSE_LIMIT} UTF-8 bytes")
    return package


_READ_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW


def _read_regular_at(parent_fd: int, name: str, limit: int, label: str) -> bytes:
    try:
        fd = os.open(name, _READ_FLAGS, dir_fd=parent_fd)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise PackageError(f"{label} must be a regular non-symlink file")
            chunks: list[bytes] = []
            remaining = limit + 1
            while remaining:
                chunk = os.read(fd, min(remaining, 65536))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        finally:
            os.close(fd)
    except PackageError:
        raise
    except OSError as exc:
        raise PackageError(f"cannot read {label}: {exc}") from exc
    value = b"".join(chunks)
    if len(value) > limit:
        raise PackageError(f"{label} exceeds {limit} UTF-8 bytes")
    return value


def _open_parent(path: os.PathLike[str] | str, create: bool) -> tuple[int, str, str]:
    absolute = os.path.abspath(os.fspath(path))
    parent, name = os.path.split(absolute)
    if not name:
        raise PackageError("output or package path must name a file")
    fd = os.open(os.path.sep, _DIR_FLAGS)
    try:
        for component in Path(parent).parts[1:]:
            try:
                child = os.open(component, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(component, dir_fd=fd)
                child = os.open(component, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd, name, absolute
    except OSError as exc:
        os.close(fd)
        raise PackageError(f"cannot open parent directory: {exc}") from exc


def read_package(path: os.PathLike[str] | str) -> tuple[bytes, dict[str, Any]]:
    parent_fd, name, _ = _open_parent(path, False)
    try:
        data = _read_regular_at(parent_fd, name, PACKAGE_LIMIT, "package")
    finally:
        os.close(parent_fd)
    return data, validate_package_bytes(data)


def _write_new_file(path: os.PathLike[str] | str, data: bytes) -> None:
    parent_fd, name, _ = _open_parent(path, True)
    fd: int | None = None
    created = False
    try:
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o644,
            dir_fd=parent_fd,
        )
        created = True
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written == 0:
                raise OSError("write returned zero bytes")
            view = view[written:]
        os.fsync(fd)
    except OSError as exc:
        if fd is not None:
            os.close(fd)
            fd = None
        if created:
            try:
                os.unlink(name, dir_fd=parent_fd)
            except OSError:
                pass
        raise PackageError(f"cannot create output package: {exc}") from exc
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent_fd)


def pack_directory(directory: os.PathLike[str] | str, output: os.PathLike[str] | str) -> dict[str, Any]:
    source_absolute = os.path.abspath(os.fspath(directory))
    output_absolute = os.path.abspath(os.fspath(output))
    if os.path.commonpath((source_absolute, output_absolute)) == source_absolute:
        raise PackageError("output must be outside the input directory")
    expected = {"manifest.json", "main.js", "LICENSE"}
    try:
        source_fd = os.open(source_absolute, _DIR_FLAGS)
    except OSError as exc:
        raise PackageError(f"input must be a non-symlink directory: {exc}") from exc
    try:
        try:
            entries = set(os.listdir(source_fd))
        except OSError as exc:
            raise PackageError(f"cannot list input directory: {exc}") from exc
        if entries != expected:
            raise PackageError("input directory must contain only manifest.json, main.js, and LICENSE")
        manifest_data = _read_regular_at(source_fd, "manifest.json", MANIFEST_LIMIT, "manifest")
        source_data = _read_regular_at(source_fd, "main.js", SOURCE_LIMIT, "source")
        license_data = _read_regular_at(source_fd, "LICENSE", LICENSE_LIMIT, "licenseText")
    finally:
        os.close(source_fd)

    manifest = validate_manifest(_load_json(manifest_data, MANIFEST_LIMIT, "manifest"))
    try:
        source = source_data.decode("utf-8", "strict")
        license_text = license_data.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise PackageError("source and licenseText must be strict UTF-8") from exc
    package = {"packageVersion": 1, "manifest": manifest, "source": source, "licenseText": license_text}
    data = canonical_json(package) + b"\n"
    validate_package_bytes(data)
    _write_new_file(output_absolute, data)
    return descriptor(data, package)


def descriptor(data: bytes, package: dict[str, Any]) -> dict[str, Any]:
    return {
        "manifest": package["manifest"],
        "artifact": {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    pack = subparsers.add_parser("pack", help="pack a fixed three-file directory")
    pack.add_argument("directory")
    pack.add_argument("output")
    verify = subparsers.add_parser("verify", help="verify a .webink-plugin.json file")
    verify.add_argument("package")
    args = parser.parse_args(argv)
    try:
        if args.command == "pack":
            result = pack_directory(args.directory, args.output)
        else:
            data, package = read_package(args.package)
            result = descriptor(data, package)
    except PackageError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, separators=(",", ":")), file=sys.stderr)
        return 1
    print(json.dumps({"valid": True, **result}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
