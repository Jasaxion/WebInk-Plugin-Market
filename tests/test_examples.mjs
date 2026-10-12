import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import vm from "node:vm";

const root = path.resolve(import.meta.dirname, "..");
const encoder = new TextEncoder();

function codedError(code) {
  return Object.assign(new Error(code), { code });
}

// This mock only tests repository-owned examples; it is not a host or security sandbox.
function mockApi(permissions, options = {}) {
  const calls = [];
  const stored = new Map();
  const requirePermission = permission => {
    if (!permissions.includes(permission)) throw codedError("PERMISSION_DENIED");
  };
  const api = {
    selection: { read: async () => {
      calls.push(["selection.read"]);
      requirePermission("selection.read");
      return { text: options.selection ?? "" };
    } },
    page: {
      info: async () => {
        calls.push(["page.info"]);
        requirePermission("page.info");
        return { url: "https://example.org/article", title: "Mock page", kind: "online" };
      },
      setStyle: async style => {
        calls.push(["page.setStyle", style]);
        requirePermission("page.style");
        assert.ok([100, 125, 150].includes(style.textZoom));
      },
      clearStyle: async () => {
        calls.push(["page.clearStyle"]);
        requirePermission("page.style");
      },
    },
    network: { get: async url => {
      calls.push(["network.get", url]);
      requirePermission("network");
      assert.equal(new URL(url).origin, "https://en.wiktionary.org");
      return options.networkResponse ?? { status: 200, text: '{"query":{"pages":{"1":{"extract":"Mock definition"}}}}' };
    } },
    storage: {
      get: async key => { requirePermission("storage"); return stored.get(key) ?? null; },
      set: async (key, value) => {
        requirePermission("storage");
        if (encoder.encode(key).length > 64 || encoder.encode(JSON.stringify(value)).length > 16384) throw codedError("LIMIT_EXCEEDED");
        const candidate = new Map(stored); candidate.set(key, value);
        if (candidate.size > 100 || [...candidate].reduce((n, [k, v]) => n + encoder.encode(k).length + encoder.encode(JSON.stringify(v)).length, 0) > 65536) throw codedError("LIMIT_EXCEEDED");
        stored.set(key, value);
      },
      remove: async key => { requirePermission("storage"); stored.delete(key); },
    },
  };
  return { api, calls };
}

async function loadExample(name) {
  const directory = path.join(root, "examples", name);
  const manifest = JSON.parse(await readFile(path.join(directory, "manifest.json"), "utf8"));
  const source = await readFile(path.join(directory, "main.js"), "utf8");
  const context = vm.createContext({});
  vm.runInContext(source, context, { filename: `${name}/main.js`, timeout: 1000 });
  assert.equal(typeof context.pluginMain, "function");
  return { manifest, main: context.pluginMain };
}

function checkResult(result) {
  assert.ok(result === undefined || (result && typeof result.text === "string" && Object.keys(result).length === 1));
  if (result) assert.ok(encoder.encode(result.text).length <= 8192);
}

{
  const { manifest, main } = await loadExample("selection-statistics");
  assert.deepEqual(manifest.permissions, ["selection.read"]);
  assert.deepEqual(manifest.commands.map(command => command.id), ["count"]);
  const mock = mockApi(manifest.permissions, { selection: "Hi 👩‍💻 café" });
  const result = await main("count", mock.api);
  checkResult(result);
  assert.equal(result.text, "11 characters, 2 words");
  assert.deepEqual(mock.calls, [["selection.read"]]);
  const denied = mockApi([]);
  assert.equal((await main("count", denied.api)).text, "Selection access was denied.");
  await assert.rejects(main("unknown", mock.api), /Unsupported command/);
}

{
  const { manifest, main } = await loadExample("reading-comfort");
  assert.deepEqual(manifest.permissions, ["page.style"]);
  const mock = mockApi(manifest.permissions);
  checkResult(await main("enlarge", mock.api));
  checkResult(await main("clear", mock.api));
  assert.deepEqual(JSON.parse(JSON.stringify(mock.calls)), [["page.setStyle", { textZoom: 125 }], ["page.clearStyle"]]);
  await assert.rejects(main("unknown", mock.api), /Unsupported command/);
}

{
  const { manifest, main } = await loadExample("public-origin-lookup");
  assert.deepEqual(manifest.permissions, ["selection.read", "network"]);
  assert.deepEqual(manifest.networkOrigins, ["https://en.wiktionary.org"]);
  const mock = mockApi(manifest.permissions, { selection: "ice cream/雪" });
  const result = await main("lookup", mock.api);
  checkResult(result);
  assert.equal(result.text, "Mock definition");
  assert.match(mock.calls[1][1], /titles=ice%20cream%2F%E9%9B%AA$/);
  const long = mockApi(manifest.permissions, {
    selection: "term",
    networkResponse: { status: 200, text: JSON.stringify({ query: { pages: { 1: { extract: "😀".repeat(3000) } } } }) },
  });
  checkResult(await main("lookup", long.api));
  await assert.rejects(main("unknown", mock.api), /Unsupported command/);
}

{
  const mock = mockApi(["storage"]);
  await assert.rejects(mock.api.storage.set("k".repeat(65), 1), error => error.code === "LIMIT_EXCEEDED");
  await assert.rejects(mock.api.storage.set("k", "x".repeat(16384)), error => error.code === "LIMIT_EXCEEDED");
  for (let i = 0; i < 100; i++) await mock.api.storage.set(`k${i}`, i);
  await assert.rejects(mock.api.storage.set("overflow", 1), error => error.code === "LIMIT_EXCEEDED");
  await assert.rejects(mockApi([]).api.page.info(), error => error.code === "PERMISSION_DENIED");
}

console.log("trusted example tests passed (mock API; not real host execution)");
