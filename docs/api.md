# WebInk plugin author API v1 — candidate packet

These declarations describe the intended API for the 0.9 candidate. A distributed host and public-only black-box validation are not yet available; this page is not evidence that the methods, permission UI or failure paths have passed real-host integration.

WebInk plugins are classic JavaScript scripts. They define one asynchronous global entry point:

```js
globalThis.pluginMain = async (commandId, api) => {
  return {text: `Ran ${commandId}`};
};
```

Do not use `import`, `export default`, modules, HTML, assets, filesystem or process APIs. There is no page JavaScript/DOM/CSS access, credentials, cookies, background execution or arbitrary host bridge. Each command gets a fresh isolate and one invocation; JavaScript state is not retained. Initialization has no capabilities and is limited to 2 seconds. An invocation is limited to 15 seconds and an 8 MiB JavaScript heap; this is not a whole-process memory limit.

The declaration file is [`sdk/webink.d.ts`](../sdk/webink.d.ts). A command must resolve to `undefined` or exactly `{text: string}`, with text at most 8 KiB UTF-8. Returned text is displayed as plain native text.

## Methods

| Method | Result | Permission and limits |
| --- | --- | --- |
| `api.page.info()` | `{url,title,kind}`; kind is `online`, `snapshot` or `reader` | `page.info`; URL is source metadata, not an identity proof, and exposes no private local path |
| `api.selection.read()` | `{text}` | `selection.read`; the same top-level ordinary selection snapshot captured when the user invokes the command; delayed calls do not reread it, at most 8 KiB; no forms, iframe or HTML data; oversize fails rather than truncates |
| `api.storage.get(key)` | JSON value or `null` | `storage` |
| `api.storage.set(key,value)` | `undefined` | `storage`; staged until command success |
| `api.storage.remove(key)` | `undefined` | `storage`; staged until command success |
| `api.network.get(url)` | `{status,text}` | `network`; HTTPS GET to an exact declared origin, no headers, credentials, referrer or redirect; at most 4 requests/invocation and 64 KiB UTF-8 response each |
| `api.page.setStyle({textZoom})` | `undefined` | `page.style`; zoom is exactly `100`, `125` or `150` |
| `api.page.clearStyle()` | `undefined` | `page.style`; clears only this plugin's current-document effect |

Storage keys are nonempty UTF-8 strings of at most 64 bytes; prototype-special keys are rejected. A plugin has at most 100 keys, 64 KiB total encoded data and 16 KiB encoded JSON per value. JSON accepts null, booleans, strings, arrays, objects and signed 64-bit integers, but no floats. JavaScript `number` cannot exactly represent every 64-bit integer: plugins must pass only values for which `Number.isSafeInteger(value)` is true. Host-provided JSON integers may be in the wider 64-bit domain; API v1 adds no BigInt wire representation.

Network origins in a manifest are exact lowercase HTTPS origins such as `https://en.wiktionary.org`; an origin does not authorize subdomains or redirects. The host also rejects local/private destinations after DNS resolution. GET can have remote side effects and cannot be rolled back. Combining network with page or selection permissions can disclose that content to the declared service.

## Failures

The candidate contract requires ordinary permission, network and storage refusals to reject the individual Promise with a typed error. Revocation, lost document context, cancellation, invocation timeout, invalid protocol or resource exhaustion terminates the whole invocation and discards staged effects. The actual host must pass these paths before SDK acceptance. Error `code` values are:

`INVALID_PACKAGE`, `INCOMPATIBLE`, `PERMISSION_DENIED`, `CONTEXT_GONE`, `REVOKED`, `BUSY`, `LIMIT_EXCEEDED`, `NETWORK_DENIED`, `NETWORK`, `TLS`, `TIMEOUT`, `CANCELLED`, `PLUGIN_ERROR`, `STORAGE`, `RUNTIME_UNAVAILABLE`.

Messages do not expose raw credentials, full page content or private host details. Handle only failures for which the command can give a useful result; otherwise rethrow. An exception or cancellation discards staged storage and page effects. Network side effects cannot be undone.
