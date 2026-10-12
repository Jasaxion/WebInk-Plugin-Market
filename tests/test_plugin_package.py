import copy
import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import plugin_package as pp

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "fixtures/package-v1/valid"


class PluginPackageTest(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((FIXTURE / "manifest.json").read_text())

    def package(self, manifest=None, source="globalThis.pluginMain = () => {};", license_text="license"):
        return {"packageVersion": 1, "manifest": manifest if manifest is not None else copy.deepcopy(self.manifest), "source": source, "licenseText": license_text}

    def encoded(self, package):
        return json.dumps(package, ensure_ascii=False, separators=(",", ":")).encode()

    def rejects(self, package):
        with self.assertRaises(pp.PackageError):
            pp.validate_package_bytes(self.encoded(package))

    def test_deterministic_pack_roundtrip_cli_and_vector(self):
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "first.webink-plugin.json"
            second = Path(temp) / "second.webink-plugin.json"
            one = pp.pack_directory(FIXTURE, first)
            two = pp.pack_directory(FIXTURE, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(one, two)
            data, package = pp.read_package(first)
            self.assertEqual(one, pp.descriptor(data, package))
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools/plugin_package.py"), "verify", str(first)],
                check=True, capture_output=True, text=True,
            )
            self.assertTrue(json.loads(result.stdout)["valid"])
        vector = json.loads((ROOT / "fixtures/package-v1/vectors.json").read_text())
        self.assertEqual(vector["canonicalManifest"].encode(), pp.canonical_json(self.manifest))

    def test_exact_lexical_and_byte_limits(self):
        for field, maximum in (("name", 80), ("description", 500), ("author", 100)):
            manifest = copy.deepcopy(self.manifest)
            manifest[field] = "😀" * maximum
            pp.validate_package_bytes(self.encoded(self.package(manifest)))
            manifest[field] += "x"
            self.rejects(self.package(manifest))
        pp.validate_package_bytes(self.encoded(self.package(source="é" * (pp.SOURCE_LIMIT // 2))))
        self.rejects(self.package(source="é" * (pp.SOURCE_LIMIT // 2) + "a"))
        pp.validate_package_bytes(self.encoded(self.package(license_text="é" * (pp.LICENSE_LIMIT // 2))))
        self.rejects(self.package(license_text="é" * (pp.LICENSE_LIMIT // 2) + "a"))
        with self.assertRaises(pp.PackageError):
            pp.validate_package_bytes(b" " * (pp.PACKAGE_LIMIT + 1))

    def test_json_grammar_duplicate_unknown_numbers_utf8_surrogates_and_prototype(self):
        valid = self.encoded(self.package())
        cases = [
            valid[:-1] + b" trailing",
            b'{"packageVersion":1,"packageVersion":1,"manifest":{},"source":"x","licenseText":"x"}',
            valid.replace(b'"packageVersion":1', b'"packageVersion":NaN'),
            valid.replace(b'"packageVersion":1', b'"packageVersion":1.0'),
            valid.replace(b'"source":"', b'"__proto__":{},"source":"'),
            valid.replace(b'"source":"', b'"source":"\\ud800'),
            b"\xff",
        ]
        for data in cases:
            with self.subTest(data=data[:40]), self.assertRaises(pp.PackageError):
                pp.validate_package_bytes(data)
        package = self.package()
        package["extra"] = 1
        self.rejects(package)
        vector = json.loads((ROOT / "fixtures/package-v1/vectors.json").read_text())["depth"]
        for kind in ("array", "object"):
            pp._load_json(vector[f"{kind}12Accepted"].encode(), 1000, "test")
            with self.assertRaises(pp.PackageError):
                pp._load_json(vector[f"{kind}13Rejected"].encode(), 1000, "test")
        self.assertEqual("scalar", pp._load_json(b'"scalar"', 1000, "test"))
        pp._load_json(b'{"key":{"nested":0}}', 1000, "test")
        with self.assertRaises(pp.PackageError):
            pp._load_json(("[" + ",".join("0" for _ in range(101)) + "]").encode(), 1000, "test")

    def test_all_manifest_fields_reject_hostile_types_and_cli_stays_json(self):
        hostile = [[], {}, None, True, 7]
        for field in self.manifest:
            for value in hostile:
                manifest = copy.deepcopy(self.manifest)
                manifest[field] = [None] if field in ("permissions", "networkOrigins") and value == [] else value
                with self.subTest(field=field, value=type(value).__name__):
                    self.rejects(self.package(manifest))
        for field in ("id", "title"):
            for value in hostile:
                manifest = copy.deepcopy(self.manifest)
                manifest["commands"][0][field] = value
                with self.subTest(command_field=field, value=type(value).__name__):
                    self.rejects(self.package(manifest))
        for field in ("packageVersion", "manifest", "source", "licenseText"):
            package = self.package()
            package[field] = []
            with self.subTest(package_field=field):
                self.rejects(package)
        self.rejects(self.package({}))
        with tempfile.TemporaryDirectory() as temp:
            bad = Path(temp) / "bad.webink-plugin.json"
            package = self.package(); package["manifest"]["license"] = []
            bad.write_bytes(self.encoded(package))
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools/plugin_package.py"), "verify", str(bad)],
                capture_output=True, text=True,
            )
            self.assertEqual(1, result.returncode)
            self.assertFalse(json.loads(result.stderr)["valid"])
            self.assertNotIn("Traceback", result.stderr)

    def test_manifest_versions_ids_commands_and_fields(self):
        mutations = []
        for key, value in [
            ("manifestVersion", True), ("apiVersion", 2), ("version", "1.0.0-beta"),
            ("version", "01.0.0"), ("version", "65536.0.0"),
            ("minHostVersion", "0.9.1"), ("id", "plain"), ("id", "A.bad"),
            ("repository", "http://github.com/a/b"), ("repository", "https://gitlab.com/a/b"),
            ("license", "GPL-3.0"),
        ]:
            manifest = copy.deepcopy(self.manifest); manifest[key] = value; mutations.append(manifest)
        manifest = copy.deepcopy(self.manifest); manifest["unknown"] = 1; mutations.append(manifest)
        manifest = copy.deepcopy(self.manifest); manifest["commands"] = []; mutations.append(manifest)
        manifest = copy.deepcopy(self.manifest); manifest["commands"] *= 9; mutations.append(manifest)
        manifest = copy.deepcopy(self.manifest); manifest["commands"] *= 2; mutations.append(manifest)
        manifest = copy.deepcopy(self.manifest); manifest["commands"][0]["id"] = "Bad"; mutations.append(manifest)
        manifest = copy.deepcopy(self.manifest); manifest["commands"][0]["extra"] = 1; mutations.append(manifest)
        for manifest in mutations:
            with self.subTest(manifest=manifest): self.rejects(self.package(manifest))

    def test_cross_client_integer_and_ip_like_origin_boundaries(self):
        for value in (-(1 << 63), (1 << 63) - 1):
            self.assertEqual(value, pp._load_json(str(value).encode(), 100, "number"))
        for value in (-(1 << 63) - 1, 1 << 63):
            with self.assertRaises(pp.PackageError):
                pp._load_json(str(value).encode(), 100, "number")
        for host in ("999.1", "127.1", "1.2.3.999"):
            manifest = copy.deepcopy(self.manifest)
            manifest["permissions"] = ["network"]
            manifest["networkOrigins"] = ["https://" + host]
            self.rejects(self.package(manifest))

    def test_permissions_and_origins(self):
        bad = [
            (["bad"], []), (["storage", "storage"], []), ([], ["https://example.com"]),
            (["network"], []), (["network"], ["https://example.com"] * 2),
            (["network"], [f"https://{name}" for name in ("localhost", "x.local", "127.0.0.1", "[::1]", "*.example.com")]),
            (["network"], ["http://example.com"]), (["network"], ["https://u@example.com"]),
            (["network"], ["https://example.com:443"]), (["network"], ["https://example.com/"]),
            (["network"], ["https://example.com?q=1"]),
        ]
        for permissions, origins in bad:
            manifest = copy.deepcopy(self.manifest)
            manifest["permissions"], manifest["networkOrigins"] = permissions, origins
            self.rejects(self.package(manifest))
        manifest = copy.deepcopy(self.manifest)
        manifest["permissions"] = ["network"]
        manifest["networkOrigins"] = ["https://api.example.com"]
        pp.validate_package_bytes(self.encoded(self.package(manifest)))

    def test_pack_rejects_extra_symlink_nested_output_and_bad_inputs_without_execution(self):
        sentinel = Path(tempfile.gettempdir()) / "webink-plugin-source-ran"
        sentinel.unlink(missing_ok=True)
        malicious = f'import os; open({str(sentinel)!r}, "w").write("ran")'
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            source.mkdir()
            (source / "manifest.json").write_text(json.dumps(self.manifest))
            (source / "main.js").write_text(malicious)
            (source / "LICENSE").write_text("license")
            outside = Path(temp) / "out.webink-plugin.json"
            pp.pack_directory(source, outside)
            self.assertFalse(sentinel.exists())
            with self.assertRaises(pp.PackageError): pp.pack_directory(source, source / "nested.webink-plugin.json")
            (source / "extra").write_text("x")
            with self.assertRaises(pp.PackageError): pp.pack_directory(source, outside)
            (source / "extra").unlink()
            (source / "main.js").unlink()
            (source / "main.js").symlink_to(source / "LICENSE")
            with self.assertRaises(pp.PackageError): pp.pack_directory(source, outside)
            with self.assertRaises(pp.PackageError): pp.pack_directory(outside, Path(temp) / "bad")
            directory_link = Path(temp) / "source-link"
            directory_link.symlink_to(source, target_is_directory=True)
            with self.assertRaises(pp.PackageError): pp.pack_directory(directory_link, outside)
        self.assertFalse(sentinel.exists())

    def test_existing_output_symlink_and_verify_nonregular_are_rejected_without_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            existing = root / "existing.webink-plugin.json"
            existing.write_bytes(b"keep")
            with self.assertRaises(pp.PackageError): pp.pack_directory(FIXTURE, existing)
            self.assertEqual(b"keep", existing.read_bytes())
            target = root / "target"
            target.write_bytes(b"target")
            link = root / "link.webink-plugin.json"
            link.symlink_to(target)
            with self.assertRaises(pp.PackageError): pp.pack_directory(FIXTURE, link)
            self.assertEqual(b"target", target.read_bytes())
            with self.assertRaises(pp.PackageError): pp.read_package(link)
            fifo = root / "fifo"
            os.mkfifo(fifo)
            completed = []
            thread = threading.Thread(target=lambda: completed.append(self._read_rejected(fifo)))
            thread.start(); thread.join(1)
            self.assertFalse(thread.is_alive(), "FIFO read blocked")
            self.assertEqual([True], completed)

    def _read_rejected(self, path):
        try:
            pp.read_package(path)
        except pp.PackageError:
            return True
        return False

    def test_controlled_swap_does_not_follow_replacement_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            source.mkdir()
            for name in ("manifest.json", "main.js", "LICENSE"):
                (source / name).write_bytes((FIXTURE / name).read_bytes())
            outside = Path(temp) / "outside"
            outside.write_text("not source")
            real_listdir = os.listdir
            def swap(fd):
                names = real_listdir(fd)
                (source / "main.js").unlink()
                (source / "main.js").symlink_to(outside)
                return names
            with mock.patch.object(pp.os, "listdir", side_effect=swap):
                with self.assertRaises(pp.PackageError):
                    pp.pack_directory(source, Path(temp) / "output")

    def test_stage_failure_cleans_private_context_without_touching_final_name(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "new.webink-plugin.json"
            real_write = os.write
            calls = 0
            def fail_after_write(fd, data):
                nonlocal calls
                calls += 1
                if calls > 1:
                    raise OSError("controlled failure")
                return real_write(fd, data[:1])
            with mock.patch.object(pp.os, "write", side_effect=fail_after_write):
                with self.assertRaises(pp.PackageError): pp.pack_directory(FIXTURE, output)
            self.assertFalse(output.exists())
            self.assertEqual([], list(Path(temp).iterdir()))

            def create_final_then_fail(fd, data):
                output.write_bytes(b"concurrent user file")
                raise OSError("controlled concurrent failure")
            with mock.patch.object(pp.os, "write", side_effect=create_final_then_fail):
                with self.assertRaises(pp.PackageError): pp.pack_directory(FIXTURE, output)
            self.assertEqual(b"concurrent user file", output.read_bytes())
            self.assertEqual([output], list(Path(temp).iterdir()))

            existing = Path(temp) / "existing"
            existing.write_bytes(b"keep")
            with mock.patch.object(pp.os, "write", side_effect=OSError("must not run")):
                with self.assertRaises(pp.PackageError): pp.pack_directory(FIXTURE, existing)
            self.assertEqual(b"keep", existing.read_bytes())

    def test_cleanup_failure_after_publication_reports_success_and_keeps_output(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "published.webink-plugin.json"
            with mock.patch.object(pp.os, "rmdir", side_effect=OSError("controlled cleanup failure")):
                result = pp.pack_directory(FIXTURE, output)
            self.assertTrue(output.is_file())
            self.assertEqual(output.stat().st_size, result["artifact"]["bytes"])

    def test_oversized_manifest_and_archive_bytes_rejected(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["description"] = "x" * 501
        self.rejects(self.package(manifest))
        for archive in (b"PK\x03\x04zip", b"ustar\x00tar"):
            with self.assertRaises(pp.PackageError): pp.validate_package_bytes(archive)


if __name__ == "__main__":
    unittest.main()
