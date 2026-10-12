# WebInk public catalog data format v1

This is the standalone public contract for catalog **data** in WebInk 0.9. It does not provide a runtime, SDK, downloader, catalog client, freshness service, or claim that a pull request certifies itself. Validation is offline and never executes package source.

## Commands

```sh
python3 tools/plugin_catalog.py verify index.json
python3 tools/plugin_catalog.py verify index.json --previous trusted-index.json
python3 tools/plugin_catalog.py build catalog.json output/index.json
python3 tools/plugin_catalog.py verify-artifact index.json package.webink-plugin.json PLUGIN_ID VERSION
```

Inputs are bounded regular non-symlink files. `build` takes one already valid catalog, writes sorted compact UTF-8 JSON plus one LF, requires output outside the input path, and never overwrites an existing name. It is deterministic; release descriptors must already contain their reviewed pinned URL, byte count, and digest. `verify-artifact` selects the required ID/version, checks exact bytes and SHA-256, and deep-matches its package manifest after full package validation. It makes no network request and does not execute, import, or compile `source`.

## Exact schema

The top-level object has exactly:

```json
{"catalogVersion":1,"sequence":1,"releases":[],"revocations":[]}
```

`catalogVersion` is integer 1. `sequence` is an integer from 1 through 9007199254740991. Each list has at most 100 entries. Empty lists are the documented initial repository state; fixture entries and examples are not market-installable releases.

A release has exactly:

```json
{
  "manifest": {"...":"the complete package manifest v1"},
  "artifact": {
    "url":"https://raw.githubusercontent.com/Jasaxion/WebInk-Plugin-Market/0123456789abcdef0123456789abcdef01234567/packages/example.plugin/0.1.0.webink-plugin.json",
    "bytes":123,
    "sha256":"64 lowercase hexadecimal characters"
  }
}
```

The manifest uses all rules in [package format v1](package-format.md), including `minHostVersion <= 0.9.0` and `apiVersion = 1`. Artifact bytes are 1–524288. The URL is exactly HTTPS on `raw.githubusercontent.com`, official owner/repository `Jasaxion/WebInk-Plugin-Market`, a 40-lowercase-hex commit, and `packages/<manifest.id>/<manifest.version>.webink-plugin.json`. Userinfo, query, fragment, redirects, wildcard, encoded path segments, branches, forks, private origins, and alternate repositories are not accepted.

Every `(id, version)` is unique. For all releases of an ID, `author` and `repository` stay constant. Versions are immutable identities, not mutable “latest” labels; clients compare stable semver components numerically (for example 0.10.0 is newer than 0.9.0) and must exclude revoked/incompatible releases when selecting an update.

A revocation has exactly `{id,version,sha256,reason}`. Its target release must exist and its digest must match. `reason` is 1–500 Unicode code points, and a target appears at most once. Revoking every version requires one entry per release; there is no all-version wildcard.

## Parsing and update identity

Decoded catalog UTF-8 is at most 4 MiB. Catalog parsing reuses the package parser rules: strict UTF-8, no lone surrogate, duplicate/unknown/prototype-special key, fraction/exponent/non-finite number, signed-64-bit overflow, container over 100 elements, or structural depth over 12. Root container depth is 1; scalar keys add no depth. JSON Schema alone cannot enforce duplicate-key rejection.

With `--previous`, the previous catalog is trusted state. Different bytes must increase sequence; old releases cannot be removed or rewritten (manifest, URL, bytes, and digest are all frozen), and old revocations cannot be removed or rewritten. New releases and matching revocations may only append semantically. These rules prevent a rewritten latest release from causing automatic downgrade, but are format/identity checks—not malware review, maintainer authorization, freshness, or fresh-client anti-replay certification.

## Publishing and trust

Publishing is two steps: a maintainer reviews and packages the immutable descriptor/package data, then publishes a descriptor pinned to the reviewed repository commit. Digest verification proves downloaded bytes match that descriptor; it does not prove the source is safe, permissions are appropriate, authorship is genuine, or review occurred. Permissions and requested network origins remain policy/review decisions.

PR validation must use base-controlled tooling and must treat candidate files only as data. It must never execute candidate source, hooks, generators, or a validator supplied by the PR head. Old release descriptors and revocations are retained. A client must also pin the catalog origin/path, retain trusted sequence/bytes and learned revocations, and reject rollback; this repository tool does not implement those client duties. Offline clients cannot learn newly published revocations, an explicit limitation.
