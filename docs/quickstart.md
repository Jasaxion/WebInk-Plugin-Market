# Author quickstart

> WebInk 0.9 candidate host distribution is queued, not published. These files define the author contract and test examples; they do not prove execution by a distributed host.

Create a directory containing exactly `manifest.json`, `main.js` and `LICENSE`. Start from one of [`examples/`](../examples/), then choose a unique namespaced ID, accurate author/repository/license metadata, commands, and only the permissions used. `main.js` defines `globalThis.pluginMain` as documented in the [API](api.md). Do not put a README or any other file inside the package directory.

On Python 3 and a supported POSIX platform, pack and verify without executing source:

```sh
python3 tools/plugin_package.py pack path/to/plugin candidate.webink-plugin.json
python3 tools/plugin_package.py verify candidate.webink-plugin.json
```

Put output outside the input directory. The reviewed Python tool requires strict three-file input and refuses replacement or symlink tricks. Node.js is optional for repository-owned example tests:

```sh
node --test tests/test_examples.mjs
```

Those tests execute only trusted source shipped in this repository using an explicit mock API. The mock is not a host, security sandbox or evidence of endpoint authority. Package and pull-request validators never execute submitted plugin code.

For local debugging, call your trusted `pluginMain` with a hand-written mock exposing only requested methods. Use synthetic page/selection data, catch typed error codes, and inspect plain results; do not inject source into a browser page or depend on private host context. Re-run `pack` and `verify` after every source or manifest change.

## Submission and review

Submit the three source files and the generated `.webink-plugin.json` candidate to this repository for maintainer review. Maintainers verify metadata, license/source provenance, permission minimization, endpoint use, source behavior and deterministic package bytes. A pull request is data for review and is never automatically executed. After merge, maintainers publish a descriptor pinned to the reviewed package commit in the catalog; authors must not invent catalog URLs or edit descriptors around that flow. Installation will become available only through a candidate host distribution when published.

See [package format](package-format.md), [API](api.md), and [permissions](permissions.md). Public Internet services can fail or change; network plugins must report useful failures and cannot promise service uptime.
