import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import plugin_catalog as pc
import plugin_package as pp

ROOT = Path(__file__).parents[1]
MANIFEST = json.loads((ROOT / "fixtures/package-v1/valid/manifest.json").read_text())
COMMIT = "a" * 40


class PluginCatalogTest(unittest.TestCase):
    def release(self, manifest=None, data=b"package"):
        manifest = copy.deepcopy(manifest or MANIFEST)
        return {
            "manifest": manifest,
            "artifact": {
                "url": f"https://raw.githubusercontent.com/Jasaxion/WebInk-Plugin-Market/{COMMIT}/packages/{manifest['id']}/{manifest['version']}.webink-plugin.json",
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            },
        }

    def catalog(self, releases=None, revocations=None, sequence=1):
        return {"catalogVersion": 1, "sequence": sequence, "releases": releases or [], "revocations": revocations or []}

    def encoded(self, value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()

    def rejects(self, value):
        with self.assertRaises(pc.CatalogError):
            pc.validate_catalog_bytes(self.encoded(value))

    def test_empty_fixture_and_multiple_versions(self):
        fixture = (ROOT / "fixtures/catalog-v1/index.json").read_bytes()
        self.assertEqual(self.catalog(), pc.validate_catalog_bytes(fixture))
        newer = copy.deepcopy(MANIFEST); newer["version"] = "0.10.0"
        catalog = self.catalog([self.release(), self.release(newer)])
        self.assertEqual(catalog, pc.validate_catalog_bytes(self.encoded(catalog)))

    def test_exact_schema_numbers_limits_and_shared_parser(self):
        vectors = json.loads((ROOT / "fixtures/catalog-v1/vectors.json").read_text())
        for vector in vectors["accepted"]:
            pc.validate_catalog_bytes(vector["json"].encode())
        for vector in vectors["rejected"]:
            with self.subTest(vector=vector["name"]), self.assertRaises(pc.CatalogError):
                pc.validate_catalog_bytes(vector["json"].encode())
        for sequence in (1, pc.MAX_SAFE_SEQUENCE):
            pc.validate_catalog_bytes(self.encoded(self.catalog(sequence=sequence)))
        for sequence in (0, pc.MAX_SAFE_SEQUENCE + 1, True, 1.0):
            self.rejects(self.catalog(sequence=sequence))
        for key in ("date", "unknown"):
            catalog = self.catalog(); catalog[key] = "2025-01-01"; self.rejects(catalog)
        release = self.release(); release["artifact"]["date"] = "today"; self.rejects(self.catalog([release]))
        self.rejects(self.catalog([self.release()] * 101))
        duplicate = b'{"catalogVersion":1,"sequence":1,"sequence":2,"releases":[],"revocations":[]}'
        for data in (duplicate, b'\xff', b'{"catalogVersion":1,"sequence":1,"releases":[],"revocations":[],"x":"\\ud800"}', b" " * (pc.CATALOG_LIMIT + 1)):
            with self.assertRaises(pc.CatalogError): pc.validate_catalog_bytes(data)
        with self.assertRaises(pc.CatalogError):
            pc.validate_catalog_bytes(b'{"catalogVersion":1,"sequence":9223372036854775808,"releases":[],"revocations":[]}')

    def test_release_identity_artifact_and_url_are_exact(self):
        duplicate = self.release()
        self.rejects(self.catalog([duplicate, copy.deepcopy(duplicate)]))
        changed = copy.deepcopy(MANIFEST); changed["version"] = "0.2.0"; changed["author"] = "Other"
        self.rejects(self.catalog([self.release(), self.release(changed)]))
        for field, value in (("bytes", 0), ("bytes", 524289), ("bytes", True), ("sha256", "A" * 64)):
            release = self.release(); release["artifact"][field] = value; self.rejects(self.catalog([release]))
        valid = self.release()["artifact"]["url"]
        bad_urls = [
            valid.replace("https://", "http://"), valid.replace("Jasaxion", "fork"),
            valid.replace(COMMIT, "b" * 39), valid + "?x=1", valid + "#x",
            valid.replace("/packages/", "/packages%2f"), valid.replace("raw.githubusercontent.com/", "u@raw.githubusercontent.com/"),
        ]
        for url in bad_urls:
            release = self.release(); release["artifact"]["url"] = url; self.rejects(self.catalog([release]))

    def test_revocation_matches_release_and_is_retained(self):
        release = self.release()
        revocation = {"id": MANIFEST["id"], "version": MANIFEST["version"], "sha256": release["artifact"]["sha256"], "reason": "withdrawn"}
        pc.validate_catalog_bytes(self.encoded(self.catalog([release], [revocation])))
        for mutation in (
            {**revocation, "version": "9.9.9"}, {**revocation, "sha256": "0" * 64},
            {**revocation, "reason": ""}, {**revocation, "reason": "x" * 501},
        ):
            self.rejects(self.catalog([release], [mutation]))
        self.rejects(self.catalog([release], [revocation, copy.deepcopy(revocation)]))
        previous = self.catalog([release], [revocation], 2)
        current = self.catalog([release], [], 3)
        with self.assertRaises(pc.CatalogError): pc.validate_update(self.encoded(current), self.encoded(previous))

    def test_previous_catalog_is_append_only_and_sequence_advances(self):
        old = self.release()
        previous = self.catalog([old], sequence=4)
        self.assertEqual(previous, pc.validate_update(self.encoded(previous), self.encoded(previous)))
        changed_manifest = copy.deepcopy(MANIFEST); changed_manifest["version"] = "0.2.0"
        added = self.catalog([old, self.release(changed_manifest)], sequence=5)
        pc.validate_update(self.encoded(added), self.encoded(previous))
        for current in (
            self.catalog([], sequence=5),
            self.catalog([{**old, "artifact": {**old["artifact"], "bytes": old["artifact"]["bytes"] + 1}}], sequence=5),
            self.catalog([old, self.release(changed_manifest)], sequence=4),
        ):
            with self.assertRaises(pc.CatalogError): pc.validate_update(self.encoded(current), self.encoded(previous))
        equal_sequence_different_bytes = self.encoded(previous) + b"\n"
        with self.assertRaises(pc.CatalogError): pc.validate_update(equal_sequence_different_bytes, self.encoded(previous))

    def test_artifact_manifest_bytes_hash_and_source_is_never_executed(self):
        sentinel = Path(tempfile.gettempdir()) / "webink-catalog-source-ran"
        sentinel.unlink(missing_ok=True)
        package = {"packageVersion": 1, "manifest": copy.deepcopy(MANIFEST), "source": f'import pathlib; pathlib.Path({str(sentinel)!r}).write_text("ran")', "licenseText": "license"}
        data = pp.canonical_json(package) + b"\n"
        release = self.release(data=data)
        self.assertEqual(package, pc.validate_artifact(release, data))
        self.assertFalse(sentinel.exists())
        for altered in (data + b" ", data.replace(b'"name":', b'"name":"changed", "oldName":')):
            with self.assertRaises(pc.CatalogError): pc.validate_artifact(release, altered)
        other_manifest = copy.deepcopy(MANIFEST); other_manifest["name"] = "Other"
        other_package = {**package, "manifest": other_manifest}
        other_data = pp.canonical_json(other_package) + b"\n"
        descriptor = self.release(manifest=MANIFEST, data=other_data)
        with self.assertRaises(pc.CatalogError): pc.validate_artifact(descriptor, other_data)
        self.assertFalse(sentinel.exists())

    def test_cli_verify_build_is_canonical_stable_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.json"; source.write_text('{ "sequence":1, "revocations":[], "releases":[], "catalogVersion":1 }')
            output = root / "index.json"
            command = [sys.executable, str(ROOT / "tools/plugin_catalog.py"), "build", str(source), str(output)]
            subprocess.run(command, check=True, capture_output=True, text=True)
            expected = pp.canonical_json(self.catalog()) + b"\n"
            self.assertEqual(expected, output.read_bytes())
            self.assertEqual(hashlib.sha256(expected).hexdigest(), hashlib.sha256(output.read_bytes()).hexdigest())
            failed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(1, failed.returncode)
            self.assertEqual(expected, output.read_bytes())
            verified = subprocess.run([sys.executable, str(ROOT / "tools/plugin_catalog.py"), "verify", str(output), "--previous", str(output)], check=True, capture_output=True, text=True)
            self.assertTrue(json.loads(verified.stdout)["valid"])
            same = subprocess.run([sys.executable, str(ROOT / "tools/plugin_catalog.py"), "build", str(source), str(source)], capture_output=True, text=True)
            self.assertEqual(1, same.returncode)


if __name__ == "__main__":
    unittest.main()
