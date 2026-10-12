import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
import urllib.error

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import check_submission as cs
import plugin_package as pp

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "fixtures/package-v1/valid"
BASE = "1" * 40
HEAD = "2" * 40
BASE_TREE = "3" * 40
HEAD_TREE = "4" * 40


def package_files(manifest=None, source=b"globalThis.pluginMain = () => {};", license_data=b"license"):
    manifest = manifest or json.loads((FIXTURE / "manifest.json").read_text())
    manifest_data = json.dumps(manifest, ensure_ascii=False).encode()
    package = {"packageVersion": 1, "manifest": manifest, "source": source.decode(), "licenseText": license_data.decode()}
    return {"manifest.json": manifest_data, "main.js": source, "LICENSE": license_data,
            "plugin.webink-plugin.json": pp.canonical_json(package) + b"\n"}


class FakeAPI:
    def __init__(self, files=None, base_files=None, changes=None, pull_sha=HEAD, *, wrapped=False):
        self.blobs = {}
        self.trees = {
            BASE_TREE: self._tree(base_files or {}, "base"),
            HEAD_TREE: self._tree(files or {}, "head"),
        }
        self.changes = changes
        self.pull_sha = pull_sha
        self.wrapped = wrapped
        self.commits = {
            BASE: {"sha": BASE, "commit": {"tree": {"sha": BASE_TREE}}},
            HEAD: {"sha": HEAD, "commit": {"tree": {"sha": HEAD_TREE}}},
        }

    def _tree(self, files, revision):
        entries = {}
        for path in files:
            parts = path.split("/")
            for depth in range(1, len(parts)):
                directory = "/".join(parts[:depth])
                entries[directory] = {"path": directory, "mode": "040000", "type": "tree",
                                      "sha": hashlib.sha1(f"tree {revision} {directory}".encode()).hexdigest()}
        for path, value in files.items():
            if isinstance(value, tuple):
                mode, kind, data = value
            else:
                mode, kind, data = "100644", "blob", value
            if kind == "blob":
                sha = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
                self.blobs[sha] = data
            else:
                sha = hashlib.sha1(f"{kind} {revision} {path}".encode()).hexdigest()
            entries[path] = {"path": path, "mode": mode, "type": kind, "sha": sha}
        return list(entries.values())

    def get(self, path):
        if path.startswith("/pulls/7/files"):
            if "page=1" not in path: return []
            return self.changes if self.changes is not None else [
                {"filename": p, "status": "added"} for p in self._blob_paths(HEAD_TREE)]
        if path == "/pulls/7":
            return {"head": {"sha": self.pull_sha, "repo": {"full_name": "fork/repo"}}, "base": {"sha": BASE}}
        if path.startswith("/commits/"):
            return copy.deepcopy(self.commits[path.rsplit("/", 1)[1]])
        if path.startswith("/git/trees/"):
            sha = path.split("/")[3].split("?")[0]
            return {"truncated": False, "tree": copy.deepcopy(self.trees[sha])}
        if path.startswith("/git/blobs/"):
            data = self.blobs[path.rsplit("/", 1)[1]]
            content = base64.b64encode(data).decode()
            if self.wrapped:
                content = "\r\n".join(content[i:i + 20] for i in range(0, len(content), 20))
            return {"encoding": "base64", "content": content}
        raise AssertionError(path)

    def _blob_paths(self, tree_sha):
        return [entry["path"] for entry in self.trees[tree_sha] if entry["type"] == "blob"]


def submission(files=None, plugin_id="example.data-only", version="0.1.0"):
    files = files or package_files()
    return {f"submissions/{plugin_id}/{version}/{name}": data for name, data in files.items()}


class SubmissionTest(unittest.TestCase):
    def certify(self, api): return cs.certify(api, BASE, HEAD, 7, "fork/repo")

    def test_valid_data_is_exactly_repacked_and_source_never_executes(self):
        sentinel = Path("/tmp/webink-submission-source-ran")
        sentinel.unlink(missing_ok=True)
        source = f'import os; open({str(sentinel)!r}, "w").write("ran")'.encode()
        result = self.certify(FakeAPI(submission(package_files(source=source)), wrapped=True))
        self.assertEqual("PUBLIC_PR_DATA", result["classification"])
        self.assertEqual(hashlib_sha(package_files(source=source)["plugin.webink-plugin.json"]), result["artifacts"][0]["sha256"])
        self.assertFalse(sentinel.exists())

    def test_new_version_accepts_changed_ancestor_tree_shas_and_preserves_old_blobs(self):
        old = submission()
        manifest = json.loads((FIXTURE / "manifest.json").read_text())
        manifest["version"] = "0.2.0"
        added = submission(package_files(manifest), version="0.2.0")
        changes = [{"filename": path, "status": "added"} for path in added]
        result = self.certify(FakeAPI({**old, **added}, old, changes))
        self.assertEqual(["0.2.0"], [artifact["version"] for artifact in result["artifacts"]])

    def test_rejects_partial_extra_wrong_path_and_special_tree_entries(self):
        cases = []
        partial = submission(); partial.pop(next(iter(partial))); cases.append(partial)
        extra = submission(); extra["submissions/example.data-only/0.1.0/README"] = b"x"; cases.append(extra)
        traversal = submission(); traversal["submissions/example.data-only/0.1.0/../x"] = b"x"; cases.append(traversal)
        hidden = submission(); hidden["submissions/example.data-only/0.1.0/.git"] = b"x"; cases.append(hidden)
        symlink = submission(); key = next(iter(symlink)); symlink[key] = ("120000", "blob", b"target"); cases.append(symlink)
        submodule = submission(); key = next(iter(submodule)); submodule[key] = ("160000", "commit", b""); cases.append(submodule)
        for files in cases:
            with self.subTest(paths=list(files)), self.assertRaises(cs.SubmissionError): self.certify(FakeAPI(files))

    def test_rejects_package_digest_source_and_manifest_mismatches_and_duplicate_json(self):
        mutations = []
        files = package_files(); files["main.js"] += b"x"; mutations.append(files)
        files = package_files(); files["plugin.webink-plugin.json"] += b" "; mutations.append(files)
        files = package_files(); files["manifest.json"] = files["manifest.json"].replace(b"{", b'{"id":"duplicate.id",', 1); mutations.append(files)
        manifest = json.loads((FIXTURE / "manifest.json").read_text()); manifest["version"] = "0.2.0"; mutations.append(package_files(manifest))
        for files in mutations:
            with self.assertRaises((cs.SubmissionError, pp.PackageError)): self.certify(FakeAPI(submission(files)))

    def test_rejects_policy_change_rewrite_delete_and_head_race(self):
        policy = FakeAPI({"README.md": b"changed"})
        with self.assertRaisesRegex(cs.SubmissionError, "NEEDS_MAINTAINER_REVIEW"): self.certify(policy)
        old = submission(); new = dict(old); key = next(iter(new)); new[key] += b"x"
        changes = [{"filename": key, "status": "modified"}]
        with self.assertRaises(cs.SubmissionError): self.certify(FakeAPI(new, old, changes))
        changes = [{"filename": key, "status": "removed"}]
        with self.assertRaises(cs.SubmissionError): self.certify(FakeAPI({}, old, changes))
        with self.assertRaisesRegex(cs.SubmissionError, "changed during"): self.certify(FakeAPI(submission(), pull_sha="3" * 40))

    def test_identity_is_constant_and_case_twins_rejected(self):
        first = submission()
        manifest = json.loads((FIXTURE / "manifest.json").read_text())
        manifest["version"], manifest["author"] = "0.2.0", "Other"
        combined = {**first, **submission(package_files(manifest), version="0.2.0")}
        with self.assertRaisesRegex(cs.SubmissionError, "identity changed"): self.certify(FakeAPI(combined))
        twins = submission(); path = next(iter(twins)); twins[path.upper()] = twins[path]
        changes = [{"filename": p, "status": "added"} for p in submission()]
        with self.assertRaisesRegex(cs.SubmissionError, "case-colliding"): self.certify(FakeAPI(twins, changes=changes))

    def test_tree_only_empty_extra_nested_and_bad_modes_are_rejected(self):
        for path, mode in (("submissions/empty.plugin", "040000"),
                           ("submissions/example.data-only/0.1.0/extra", "040000"),
                           ("submissions/example.data-only", "100644")):
            api = FakeAPI(submission())
            api.trees[HEAD_TREE].append({"path": path, "mode": mode, "type": "tree", "sha": "9" * 40})
            with self.subTest(path=path, mode=mode), self.assertRaises(cs.SubmissionError):
                self.certify(api)

    def test_limits_paging_and_malformed_api_are_bounded(self):
        api = FakeAPI(submission()); api.get = lambda path: [{"filename": "x", "status": "added"}] * 100 if path.startswith("/pulls/7/files") else FakeAPI.get(api, path)
        with self.assertRaisesRegex(cs.SubmissionError, "pagination"): self.certify(api)
        api = FakeAPI(submission()); api.changes = [{"filename": p, "status": "added"} for p in api._blob_paths(HEAD_TREE)]; api.trees[HEAD_TREE] = None
        with self.assertRaises(cs.SubmissionError): self.certify(api)
        for tree_value in (None, {"sha": "bad"}):
            api = FakeAPI(submission())
            api.commits[HEAD]["commit"]["tree"] = tree_value
            with self.subTest(tree=tree_value), self.assertRaises(cs.SubmissionError): self.certify(api)

    def test_blob_decoder_allows_only_cr_lf_wrapping(self):
        api = FakeAPI(submission())
        original = api.get
        def invalid_blob(path):
            value = original(path)
            if path.startswith("/git/blobs/"):
                value["content"] += "!"
            return value
        api.get = invalid_blob
        with self.assertRaisesRegex(cs.SubmissionError, "encoding is malformed"): self.certify(api)

    def test_transport_refuses_bad_paths_redirects_malformed_oversize_timeout_and_hides_token(self):
        class Response:
            def __init__(self, body=b"{}", url=cs.API_ROOT + "/x", length=None):
                self.body, self.url = body, url
                self.headers = {} if length is None else {"Content-Length": str(length)}
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def geturl(self): return self.url
            def read(self, limit): return self.body
        class Opener:
            def __init__(self, value): self.value = value
            def open(self, request, timeout):
                if isinstance(self.value, Exception): raise self.value
                return self.value
        token = "SECRET_TOKEN"
        for value in (Response(b"{"), Response(b"{}", "https://evil.example/x"),
                      Response(b"{}", length=cs.MAX_RESPONSE + 1), urllib.error.URLError("timeout")):
            with self.subTest(value=value), self.assertRaises(cs.SubmissionError) as caught:
                cs.GitHubAPI(token, Opener(value)).get("/x")
            self.assertNotIn(token, str(caught.exception))
        with self.assertRaises(cs.SubmissionError): cs.GitHubAPI(token, Opener(Response())).get("https://evil.example")


def hashlib_sha(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


if __name__ == "__main__": unittest.main()
