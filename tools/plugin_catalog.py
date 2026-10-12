#!/usr/bin/env python3
"""Validate and canonically build the WebInk v1 public catalog data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from typing import Any

import plugin_package as package_format

CATALOG_LIMIT = 4 * 1024 * 1024
MAX_SAFE_SEQUENCE = 9007199254740991
CATALOG_FIELDS = {"catalogVersion", "sequence", "releases", "revocations"}
RELEASE_FIELDS = {"manifest", "artifact"}
ARTIFACT_FIELDS = {"url", "bytes", "sha256"}
REVOCATION_FIELDS = {"id", "version", "sha256", "reason"}
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
RAW_PREFIX = "https://raw.githubusercontent.com/Jasaxion/WebInk-Plugin-Market/"


class CatalogError(ValueError):
    """The input does not satisfy public catalog format v1."""


def _object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CatalogError(f"{label} must be an object")
    unknown = set(value) - fields
    missing = fields - set(value)
    if unknown:
        raise CatalogError(f"unknown {label} field: {sorted(unknown)[0]}")
    if missing:
        raise CatalogError(f"missing {label} field: {sorted(missing)[0]}")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise CatalogError(f"{label} must be an array")
    if len(value) > 100:
        raise CatalogError(f"{label} exceeds 100 entries")
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise CatalogError(f"{label} must be 64 lowercase hexadecimal characters")
    return value


def _artifact_url(value: Any, plugin_id: str, version: str) -> str:
    if not isinstance(value, str):
        raise CatalogError("artifact url must be a string")
    suffix = f"/packages/{plugin_id}/{version}.webink-plugin.json"
    if not value.startswith(RAW_PREFIX) or not value.endswith(suffix):
        raise CatalogError("artifact url is not the pinned official repository path")
    commit = value[len(RAW_PREFIX):-len(suffix)]
    if not COMMIT_RE.fullmatch(commit):
        raise CatalogError("artifact url must contain a 40-character lowercase commit")
    expected = RAW_PREFIX + commit + suffix
    if value != expected:
        raise CatalogError("artifact url must be exact and canonical")
    return value


def validate_catalog_bytes(data: bytes) -> dict[str, Any]:
    """Parse and validate one catalog, without fetching or executing anything."""
    try:
        value = package_format._load_json(data, CATALOG_LIMIT, "catalog")
    except package_format.PackageError as exc:
        raise CatalogError(str(exc)) from exc
    catalog = _object(value, CATALOG_FIELDS, "catalog")
    if type(catalog["catalogVersion"]) is not int or catalog["catalogVersion"] != 1:
        raise CatalogError("catalogVersion must be integer 1")
    sequence = catalog["sequence"]
    if type(sequence) is not int or not 1 <= sequence <= MAX_SAFE_SEQUENCE:
        raise CatalogError(f"sequence must be an integer from 1 to {MAX_SAFE_SEQUENCE}")

    releases = _list(catalog["releases"], "releases")
    release_by_target: dict[tuple[str, str], dict[str, Any]] = {}
    identity_by_id: dict[str, tuple[str, str]] = {}
    for value in releases:
        release = _object(value, RELEASE_FIELDS, "release")
        try:
            manifest = package_format.validate_manifest(release["manifest"])
        except package_format.PackageError as exc:
            raise CatalogError(str(exc)) from exc
        artifact = _object(release["artifact"], ARTIFACT_FIELDS, "artifact")
        size = artifact["bytes"]
        if type(size) is not int or not 1 <= size <= package_format.PACKAGE_LIMIT:
            raise CatalogError(f"artifact bytes must be an integer from 1 to {package_format.PACKAGE_LIMIT}")
        _sha256(artifact["sha256"], "artifact sha256")
        _artifact_url(artifact["url"], manifest["id"], manifest["version"])
        target = (manifest["id"], manifest["version"])
        if target in release_by_target:
            raise CatalogError("duplicate release id/version")
        release_by_target[target] = release
        identity = (manifest["author"], manifest["repository"])
        if manifest["id"] in identity_by_id and identity_by_id[manifest["id"]] != identity:
            raise CatalogError("author and repository must remain constant for an id")
        identity_by_id[manifest["id"]] = identity

    seen_revocations: set[tuple[str, str]] = set()
    for value in _list(catalog["revocations"], "revocations"):
        revocation = _object(value, REVOCATION_FIELDS, "revocation")
        plugin_id = revocation["id"]
        version = revocation["version"]
        reason = revocation["reason"]
        if not isinstance(plugin_id, str) or not isinstance(version, str):
            raise CatalogError("revocation id and version must be strings")
        digest = _sha256(revocation["sha256"], "revocation sha256")
        if not isinstance(reason, str) or not 1 <= len(reason) <= 500:
            raise CatalogError("revocation reason must contain 1..500 Unicode code points")
        target = (plugin_id, version)
        if target in seen_revocations:
            raise CatalogError("duplicate revocation target")
        seen_revocations.add(target)
        release = release_by_target.get(target)
        if release is None or release["artifact"]["sha256"] != digest:
            raise CatalogError("revocation must match an existing release and digest")
    return catalog


def validate_update(current_data: bytes, previous_data: bytes) -> dict[str, Any]:
    """Validate an append-only catalog update against trusted previous bytes."""
    current = validate_catalog_bytes(current_data)
    previous = validate_catalog_bytes(previous_data)
    if current_data != previous_data and current["sequence"] <= previous["sequence"]:
        raise CatalogError("a changed catalog must increase sequence")
    if current_data == previous_data:
        return current
    previous_releases = {(r["manifest"]["id"], r["manifest"]["version"]): r for r in previous["releases"]}
    current_releases = {(r["manifest"]["id"], r["manifest"]["version"]): r for r in current["releases"]}
    for target, release in previous_releases.items():
        if target not in current_releases:
            raise CatalogError("an existing release cannot be removed")
        if current_releases[target] != release:
            raise CatalogError("an existing release cannot be rewritten")
    previous_revocations = {(r["id"], r["version"]): r for r in previous["revocations"]}
    current_revocations = {(r["id"], r["version"]): r for r in current["revocations"]}
    for target, revocation in previous_revocations.items():
        if current_revocations.get(target) != revocation:
            raise CatalogError("an existing revocation cannot be removed or rewritten")
    return current


def read_catalog(path: os.PathLike[str] | str) -> tuple[bytes, dict[str, Any]]:
    parent_fd, name, _ = package_format._open_parent(path, False)
    try:
        data = package_format._read_regular_at(parent_fd, name, CATALOG_LIMIT, "catalog")
    finally:
        os.close(parent_fd)
    return data, validate_catalog_bytes(data)


def validate_artifact(release: dict[str, Any], data: bytes) -> dict[str, Any]:
    """Validate a release, then match its exact package bytes and manifest."""
    try:
        validate_catalog_bytes(package_format.canonical_json({
            "catalogVersion": 1, "sequence": 1, "releases": [release], "revocations": [],
        }))
    except package_format.PackageError as exc:
        raise CatalogError(str(exc)) from exc
    artifact = release["artifact"]
    if len(data) != artifact["bytes"]:
        raise CatalogError("package byte count does not match release")
    if hashlib.sha256(data).hexdigest() != artifact["sha256"]:
        raise CatalogError("package sha256 does not match release")
    try:
        package = package_format.validate_package_bytes(data)
    except package_format.PackageError as exc:
        raise CatalogError(str(exc)) from exc
    if package["manifest"] != release["manifest"]:
        raise CatalogError("package manifest does not match release")
    return package


def _release(catalog: dict[str, Any], plugin_id: str, version: str) -> dict[str, Any]:
    matches = [r for r in catalog["releases"] if r["manifest"]["id"] == plugin_id and r["manifest"]["version"] == version]
    if len(matches) != 1:
        raise CatalogError("catalog does not contain exactly one selected release")
    return matches[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify", help="verify catalog data")
    verify.add_argument("index")
    verify.add_argument("--previous")
    build = commands.add_parser("build", help="verify and write canonical catalog data")
    build.add_argument("index")
    build.add_argument("output")
    artifact = commands.add_parser("verify-artifact", help="verify an offline package against a release")
    artifact.add_argument("catalog")
    artifact.add_argument("package")
    artifact.add_argument("plugin_id")
    artifact.add_argument("version")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            data, catalog = read_catalog(args.index)
            if args.previous:
                previous_data, _ = read_catalog(args.previous)
                catalog = validate_update(data, previous_data)
        elif args.command == "build":
            data, catalog = read_catalog(args.index)
            if os.path.abspath(args.index) == os.path.abspath(args.output):
                raise CatalogError("output must differ from input")
            output_data = package_format.canonical_json(catalog) + b"\n"
            package_format._write_new_file(args.output, output_data)
        else:
            _, catalog = read_catalog(args.catalog)
            package_data, _ = package_format.read_package(args.package)
            validate_artifact(_release(catalog, args.plugin_id, args.version), package_data)
        result = {"valid": True, "catalogVersion": catalog["catalogVersion"], "sequence": catalog["sequence"]}
        if args.command == "build":
            result.update(bytes=len(output_data), sha256=hashlib.sha256(output_data).hexdigest())
    except (CatalogError, package_format.PackageError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, separators=(",", ":")), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
