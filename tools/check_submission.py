#!/usr/bin/env python3
"""Certify pull-request submission data without checking out or executing it."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import plugin_package

REPOSITORY = "Jasaxion/WebInk-Plugin-Market"
API_ROOT = f"https://api.github.com/repos/{REPOSITORY}"
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
ID_RE = plugin_package.ID_RE
SEMVER_RE = plugin_package.SEMVER_RE
EXPECTED_FILES = {"manifest.json", "main.js", "LICENSE", "plugin.webink-plugin.json"}
MAX_RESPONSE = 2 * 1024 * 1024
MAX_TOTAL = 20 * 1024 * 1024
MAX_REQUESTS = 200
MAX_CHANGED_PAGES = 10


class SubmissionError(ValueError):
    """A bounded, safe submission check could not certify the candidate."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SubmissionError("GitHub API redirect refused")


class GitHubAPI:
    def __init__(self, token: str, opener=None):
        if not token:
            raise SubmissionError("GH_TOKEN is required")
        self.token = token
        self.opener = opener or urllib.request.build_opener(_NoRedirect())
        self.requests = 0
        self.total = 0

    def get(self, path: str):
        if not path.startswith("/") or "//" in path:
            raise SubmissionError("invalid API path")
        self.requests += 1
        if self.requests > MAX_REQUESTS:
            raise SubmissionError("GitHub API request quota exceeded")
        url = API_ROOT + path
        request = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "webink-submission-check/1",
        })
        try:
            with self.opener.open(request, timeout=10) as response:
                if response.geturl() != url:
                    raise SubmissionError("GitHub API changed origin or URL")
                length = response.headers.get("Content-Length")
                if length and int(length) > MAX_RESPONSE:
                    raise SubmissionError("GitHub API response is oversized")
                body = response.read(MAX_RESPONSE + 1)
        except SubmissionError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise SubmissionError("GitHub API request failed") from exc
        if len(body) > MAX_RESPONSE:
            raise SubmissionError("GitHub API response is oversized")
        self.total += len(body)
        if self.total > MAX_TOTAL:
            raise SubmissionError("GitHub API byte quota exceeded")
        try:
            return json.loads(body.decode("utf-8", "strict"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise SubmissionError("GitHub API returned malformed JSON") from exc


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise SubmissionError(f"invalid {label}")
    return value


def _safe_path(path: object) -> str:
    if not isinstance(path, str) or not path or len(path.encode("utf-8")) > 1000:
        raise SubmissionError("invalid changed path")
    parts = PurePosixPath(path).parts
    if path.startswith("/") or "\\" in path or any(part in ("", ".", "..", ".git") for part in parts):
        raise SubmissionError("unsafe changed path")
    return path


def _tree(api: GitHubAPI, sha: str) -> dict[str, dict]:
    value = api.get(f"/git/trees/{sha}?recursive=1")
    if not isinstance(value, dict) or value.get("truncated") is not False or not isinstance(value.get("tree"), list):
        raise SubmissionError("complete candidate tree is unavailable")
    result = {}
    folded = set()
    for entry in value["tree"]:
        if not isinstance(entry, dict):
            raise SubmissionError("malformed tree entry")
        path = _safe_path(entry.get("path"))
        key = path.casefold()
        if key in folded:
            raise SubmissionError("case-colliding candidate paths")
        folded.add(key)
        result[path] = entry
    return result


def _blob(api: GitHubAPI, sha: object, limit: int) -> bytes:
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        raise SubmissionError("invalid blob identity")
    value = api.get(f"/git/blobs/{sha}")
    if not isinstance(value, dict) or value.get("encoding") != "base64" or not isinstance(value.get("content"), str):
        raise SubmissionError("GitHub blob response is malformed")
    try:
        encoded = value["content"].replace("\r", "").replace("\n", "")
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeError) as exc:
        raise SubmissionError("GitHub blob encoding is malformed") from exc
    if len(data) > limit:
        raise SubmissionError("submission file exceeds its byte limit")
    return data


def _changed_files(api: GitHubAPI, number: int) -> list[dict]:
    result = []
    for page in range(1, MAX_CHANGED_PAGES + 1):
        values = api.get(f"/pulls/{number}/files?per_page=100&page={page}")
        if not isinstance(values, list):
            raise SubmissionError("changed-file response is malformed")
        result.extend(values)
        if len(values) < 100:
            return result
    raise SubmissionError("changed-file pagination limit exceeded")


def _submission_parts(path: str) -> tuple[str, str, str] | None:
    parts = path.split("/")
    if len(parts) != 4 or parts[0] != "submissions":
        return None
    plugin_id, version, name = parts[1:]
    if not ID_RE.fullmatch(plugin_id) or not SEMVER_RE.fullmatch(version) or name not in EXPECTED_FILES:
        raise SubmissionError("submission paths must use the exact four-file layout")
    return plugin_id, version, name


def _manifest(data: bytes) -> dict:
    return plugin_package.validate_manifest(plugin_package._load_json(
        data, plugin_package.MANIFEST_LIMIT, "manifest"))


def _commit_tree(api: GitHubAPI, commit_sha: str, label: str) -> str:
    commit = api.get(f"/commits/{commit_sha}")
    try:
        observed_commit = commit["sha"]
        tree_sha = commit["commit"]["tree"]["sha"]
    except (KeyError, TypeError) as exc:
        raise SubmissionError(f"{label} commit metadata is malformed") from exc
    if observed_commit != commit_sha:
        raise SubmissionError(f"{label} commit SHA does not match")
    return _sha(tree_sha, f"{label} tree SHA")


def _submission_tree(tree: dict[str, dict]) -> dict[tuple[str, str], dict[str, dict]]:
    folders: dict[tuple[str, str], dict[str, dict]] = {}
    directory_paths: set[tuple[str, ...]] = set()
    for path, entry in tree.items():
        parts = path.split("/")
        if parts[0] != "submissions":
            continue
        if entry.get("type") == "tree":
            if entry.get("mode") != "040000" or not isinstance(entry.get("sha"), str) or not SHA_RE.fullmatch(entry["sha"]):
                raise SubmissionError("submission directories must be mode-040000 Git trees")
            if not 1 <= len(parts) <= 3:
                raise SubmissionError("invalid directory beneath submissions")
            if len(parts) >= 2 and not ID_RE.fullmatch(parts[1]):
                raise SubmissionError("invalid plugin directory beneath submissions")
            if len(parts) == 3 and not SEMVER_RE.fullmatch(parts[2]):
                raise SubmissionError("invalid version directory beneath submissions")
            directory_paths.add(tuple(parts))
            continue
        parsed = _submission_parts(path)
        if parsed is None:
            raise SubmissionError("invalid entry beneath submissions")
        plugin_id, version, name = parsed
        if entry.get("type") != "blob" or entry.get("mode") != "100644" or not isinstance(entry.get("sha"), str) or not SHA_RE.fullmatch(entry["sha"]):
            raise SubmissionError("submission entries must be regular mode-0644 Git blobs")
        folders.setdefault((plugin_id, version), {})[name] = entry
    for plugin_id, version in folders:
        required = {("submissions",), ("submissions", plugin_id), ("submissions", plugin_id, version)}
        if not required <= directory_paths:
            raise SubmissionError("submission files require their Git tree hierarchy")
    expected_directories = {path for plugin_id, version in folders for path in (
        ("submissions",), ("submissions", plugin_id), ("submissions", plugin_id, version))}
    if directory_paths != expected_directories:
        raise SubmissionError("empty or extra submission directory is forbidden")
    for entries in folders.values():
        if set(entries) != EXPECTED_FILES:
            raise SubmissionError("every submission folder must contain exactly four files")
    return folders


def certify(api: GitHubAPI, base_sha: str, candidate_sha: str, pr_number: int, head_repository: str) -> dict:
    base_sha, candidate_sha = _sha(base_sha, "base SHA"), _sha(candidate_sha, "candidate SHA")
    if not 1 <= pr_number <= 2_147_483_647:
        raise SubmissionError("invalid pull request number")
    pull = api.get(f"/pulls/{pr_number}")
    try:
        observed_sha = pull["head"]["sha"]
        observed_repo = pull["head"]["repo"]["full_name"]
        observed_base = pull["base"]["sha"]
    except (KeyError, TypeError) as exc:
        raise SubmissionError("pull request metadata is malformed") from exc
    if (observed_sha, observed_repo, observed_base) != (candidate_sha, head_repository, base_sha):
        raise SubmissionError("pull request head or base changed during validation")
    base_tree_sha = _commit_tree(api, base_sha, "base")
    candidate_tree_sha = _commit_tree(api, candidate_sha, "candidate")

    changes = _changed_files(api, pr_number)
    if not changes:
        raise SubmissionError("pull request has no changed files")
    touched: set[tuple[str, str]] = set()
    for change in changes:
        if not isinstance(change, dict):
            raise SubmissionError("changed-file entry is malformed")
        path = _safe_path(change.get("filename"))
        parts = _submission_parts(path)
        if parts is None:
            raise SubmissionError(f"NEEDS_MAINTAINER_REVIEW: policy path changed: {path}")
        if change.get("status") != "added":
            raise SubmissionError("existing submission data cannot be deleted or rewritten")
        touched.add(parts[:2])

    base_tree, candidate_tree = _tree(api, base_tree_sha), _tree(api, candidate_tree_sha)
    base_folders = _submission_tree(base_tree)
    candidate_folders = _submission_tree(candidate_tree)
    for path, entry in base_tree.items():
        if not path.startswith("submissions/") or entry.get("type") == "tree":
            continue
        candidate_entry = candidate_tree.get(path)
        identity = lambda item: None if item is None else (item.get("mode"), item.get("type"), item.get("sha"))
        if identity(candidate_entry) != identity(entry):
            raise SubmissionError("existing submission snapshot changed")

    identities: dict[str, tuple[str, str]] = {}
    if not touched:
        raise SubmissionError("no submission folder was added")

    reports = []
    for folder, entries in candidate_folders.items():
        manifest = _manifest(_blob(api, entries["manifest.json"].get("sha"), plugin_package.MANIFEST_LIMIT))
        plugin_id, version = folder
        if (manifest["id"], manifest["version"]) != folder:
            raise SubmissionError("manifest id/version does not match its path")
        identity = (manifest["author"], manifest["repository"])
        if plugin_id in identities and identities[plugin_id] != identity:
            raise SubmissionError("author and repository identity changed for a plugin id")
        identities[plugin_id] = identity
        if folder not in touched:
            continue
        source = _blob(api, entries["main.js"].get("sha"), plugin_package.SOURCE_LIMIT)
        license_data = _blob(api, entries["LICENSE"].get("sha"), plugin_package.LICENSE_LIMIT)
        package_data = _blob(api, entries["plugin.webink-plugin.json"].get("sha"), plugin_package.PACKAGE_LIMIT)
        try:
            package = {"packageVersion": 1, "manifest": manifest,
                       "source": source.decode("utf-8", "strict"),
                       "licenseText": license_data.decode("utf-8", "strict")}
        except UnicodeDecodeError as exc:
            raise SubmissionError("source and license must be strict UTF-8") from exc
        expected = plugin_package.canonical_json(package) + b"\n"
        try:
            validated = plugin_package.validate_package_bytes(package_data)
        except plugin_package.PackageError as exc:
            raise SubmissionError(str(exc)) from exc
        if package_data != expected or validated != package:
            raise SubmissionError("package is not the exact deterministic repack of its three inputs")
        reports.append({"id": plugin_id, "version": version, "bytes": len(package_data),
                        "sha256": hashlib.sha256(package_data).hexdigest()})
    if set(touched) != {(item["id"], item["version"]) for item in reports}:
        raise SubmissionError("a touched submission folder is incomplete")
    final_pull = api.get(f"/pulls/{pr_number}")
    try:
        final_observation = (final_pull["head"]["sha"], final_pull["head"]["repo"]["full_name"], final_pull["base"]["sha"])
    except (KeyError, TypeError) as exc:
        raise SubmissionError("pull request metadata is malformed") from exc
    if final_observation != (candidate_sha, head_repository, base_sha):
        raise SubmissionError("pull request head or base changed during validation")
    return {"valid": True, "classification": "PUBLIC_PR_DATA", "artifacts": sorted(reports, key=lambda x: (x["id"], x["version"])),
            "manualReview": ["author/repository provenance", "source security and license", "real-host behavior", "maintainer publication"]}


def main() -> int:
    try:
        result = certify(GitHubAPI(os.environ.get("GH_TOKEN", "")),
                         os.environ.get("BASE_SHA", ""), os.environ.get("CANDIDATE_SHA", ""),
                         int(os.environ.get("PR_NUMBER", "0")), os.environ.get("HEAD_REPOSITORY", ""))
    except (SubmissionError, plugin_package.PackageError, ValueError) as exc:
        print(json.dumps({"valid": False, "classification": "REJECTED", "error": str(exc)}, separators=(",", ":")))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
