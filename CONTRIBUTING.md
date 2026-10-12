# Contributing plugin submissions

A submission is exactly:

```
submissions/<plugin-id>/<major.minor.patch>/manifest.json
submissions/<plugin-id>/<major.minor.patch>/main.js
submissions/<plugin-id>/<major.minor.patch>/LICENSE
submissions/<plugin-id>/<major.minor.patch>/plugin.webink-plugin.json
```

All four entries must be ordinary files; no hidden files, links, submodules, assets, or README are accepted. Existing versions are immutable. A later version may be added only with the same manifest `id`, `author`, and `repository`. The path ID/version must equal the manifest. One pull request for automatic data certification may change submission files only; changes to policy, workflows, tools, schemas, SDK, examples, packages, catalog, or index require a separate maintainer review and will fail this check without being labelled malicious.

Generate outside the three-file source directory, then copy the result beside those inputs:

```sh
mkdir -p /tmp/webink-build
cp manifest.json main.js LICENSE /tmp/webink-build/
python3 tools/plugin_package.py pack /tmp/webink-build /tmp/plugin.webink-plugin.json
python3 tools/plugin_package.py verify /tmp/plugin.webink-plugin.json
mkdir -p submissions/<plugin-id>/<version>
cp /tmp/webink-build/{manifest.json,main.js,LICENSE} submissions/<plugin-id>/<version>/
cp /tmp/plugin.webink-plugin.json submissions/<plugin-id>/<version>/
```

The packer intentionally accepts exactly three inputs, so do not generate its output inside the input directory. Validation reads candidate bytes at the pinned pull-request head through the GitHub API; it never checks out the candidate, installs dependencies, invokes hooks, or executes JavaScript.

Passing means only that the four files are complete, bounded, valid data and the package is their exact deterministic representation. It does not prove identity, ownership, safety, licensing, host compatibility, or publication eligibility. Maintainers manually verify provenance and namespace ownership, review source/security/license, and test in a real host. Authors cannot trigger merge, publishing, catalog edits, or index updates. After review and merge, maintainers publish the package at an official commit-pinned Market URL and update the catalog in a separate step. Manifest `repository` may name another canonical GitHub repository; it is not an approved artifact source.
