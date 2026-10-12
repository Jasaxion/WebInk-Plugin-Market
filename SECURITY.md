# Security policy

Do not include secrets in issues, pull requests, plugin files, or logs. Report suspected vulnerabilities privately through GitHub's security advisory interface for this repository.

Public pull requests are untrusted data. The submission workflow checks out only its trusted base commit and retrieves bounded candidate blobs through the fixed GitHub API origin at the exact observed head SHA. It does not execute candidate code, use candidate workflows/tools, install dependencies, expose write permissions or OIDC, or publish artifacts. A passing check is not a security or authorship endorsement; maintainers must review provenance, source, permissions, licensing, and real-host behavior before publication.
