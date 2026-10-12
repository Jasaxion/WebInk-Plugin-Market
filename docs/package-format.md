# WebInk plugin package format v1

This document is the complete public contract for package and manifest **data** in WebInk 0.9. It does not provide a plugin runtime or SDK, and validation never evaluates, imports, compiles, parses as JavaScript, or runs `main.js`.

## Authoring directory and package

Run:

```sh
python3 tools/plugin_package.py pack PATH/TO/DIRECTORY plugin.webink-plugin.json
python3 tools/plugin_package.py verify plugin.webink-plugin.json
```

The input directory must contain exactly three regular, non-symlink files: `manifest.json`, `main.js`, and `LICENSE`. The output must be outside that directory. No nested entries, ZIP/TAR archive, hook, dependency installation, or asset is supported. `main.js` is non-empty strict UTF-8 classic-script source data. The runtime will later check its entry point; this tool deliberately makes no JavaScript syntax or security claim.

The output is deterministic canonical JSON: keys sorted by Unicode code point, no insignificant whitespace, UTF-8 characters emitted directly, and one final LF. Both commands print JSON containing the validated manifest and an artifact descriptor with exact `bytes` and lowercase SHA-256. Artifact URLs and catalog indexes are outside this tool.

The package object has exactly `packageVersion`, `manifest`, `source`, and `licenseText`; `packageVersion` is integer `1`. `licenseText` must be non-empty. A license identifier is metadata, not legal certification; review remains responsible for ensuring the text and source are accurate.

## Manifest v1

A manifest has exactly these fields:

```json
{
  "manifestVersion": 1,
  "id": "example.plugin",
  "version": "0.1.0",
  "name": "Example",
  "description": "Description",
  "author": "Author",
  "repository": "https://github.com/owner/repository",
  "license": "Apache-2.0",
  "minHostVersion": "0.9.0",
  "apiVersion": 1,
  "commands": [{"id": "command", "title": "Command"}],
  "permissions": [],
  "networkOrigins": []
}
```

- `manifestVersion` and `apiVersion` are integer `1` (JSON booleans do not count as integers).
- `id` is 3–80 characters and matches `[a-z0-9]+(?:[.-][a-z0-9]+)+`.
- `version` and `minHostVersion` are stable `major.minor.patch`, with no leading zero and numeric components 0–65535. `minHostVersion` is at most `0.9.0` for this format/tool version.
- `name` and command `title` are 1–80 Unicode code points; `description` 1–500; `author` 1–100. These are lexical code-point limits, distinct from the UTF-8 byte limits below.
- `repository` is a canonical `https://github.com/owner/repository` URL, at most 2048 code points, with no suffix, query, fragment, userinfo, or explicit port.
- `license` is one of the complete v1 SPDX allowlist: `Apache-2.0`, `MIT`, `BSD-2-Clause`, `BSD-3-Clause`.
- `commands` has 1–8 unique IDs. Each command has exactly `id` and `title`; IDs match `[a-z][a-z0-9-]{0,39}`.
- `permissions` is a duplicate-free subset of `page.info`, `selection.read`, `storage`, `network`, `page.style`.
- Without `network`, `networkOrigins` is empty. With it, the list has 1–4 unique exact origins. An origin is lowercase canonical `https://host`, default port 443 only, with no path (including `/`), userinfo, query, fragment, wildcard, IP literal, `localhost`, `.localhost`, or `.local`. Runtime DNS public-address checks are separate.

## Parser and size limits

All files and package strings use strict UTF-8; malformed input and lone Unicode surrogates are rejected. JSON rejects duplicate keys, trailing data, NaN/Infinity, fractional numbers, unknown fields, and prototype-special keys `__proto__`, `prototype`, and `constructor` anywhere. Maximum nesting depth is 12; every object and array has at most 100 members/items.

Limits are read-bounded before parsing: package 512 KiB, source 256 KiB, canonical manifest 16 KiB, and license text 64 KiB. These are UTF-8 byte limits. The separate metadata lengths above count Unicode code points consistently.

`fixtures/package-v1/vectors.json` fixes the canonical valid manifest representation for future client implementations. `fixtures/package-v1/valid` is a packaging test fixture only and is explicitly not a product plugin or SDK example.
