# Permissions and lifecycle

A manifest requests only the capabilities its commands call:

- `page.info`: read current source metadata.
- `selection.read`: read the current ordinary selection.
- `storage`: access this plugin's private, unsynchronized key/value data.
- `network`: issue constrained GET requests to the manifest's exact `networkOrigins`.
- `page.style`: set one fixed text zoom or clear this plugin's effect.

Installation starts disabled. Enabling requires explicit user approval of requested permissions and origins. Denial leaves the plugin disabled. The host derives plugin identity and grants from the installed package; source cannot choose them. Mocks and tests grant no network or endpoint authority in a real host.

An update is manual and fully validated before activation. If it requests a new permission or origin, the new version remains disabled until that addition is approved; denial preserves the old version and grants. A failed update also preserves the old version.

Disabling cancels active work and keeps private storage. Revocation cancels and disables the affected version but does not delete private data or silently roll back. Offline hosts cannot know a newly published revocation; they retain all previously known revocations and show cached catalog age. Uninstall deletes private storage by default, with an explicit keep-data choice; retained data can later be purged.

Page operations require the same active document throughout the invocation and fail with `CONTEXT_GONE` after navigation, including same-URL reload. Successful style layers apply only to the current document; disabling or uninstalling removes that plugin's layer while preserving other plugins and current user settings.
