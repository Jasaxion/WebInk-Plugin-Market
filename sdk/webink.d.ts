/** WebInk plugin author API, API version 1. */
export type PageKind = "online" | "snapshot" | "reader";
export type TextZoom = 100 | 125 | 150;

/** JSON-compatible data. Number values must be finite safe integers in JavaScript. */
export type JSONValue = null | boolean | string | number | JSONValue[] | { [key: string]: JSONValue };

export type PluginErrorCode =
  | "INVALID_PACKAGE"
  | "INCOMPATIBLE"
  | "PERMISSION_DENIED"
  | "CONTEXT_GONE"
  | "REVOKED"
  | "BUSY"
  | "LIMIT_EXCEEDED"
  | "NETWORK_DENIED"
  | "NETWORK"
  | "TLS"
  | "TIMEOUT"
  | "CANCELLED"
  | "PLUGIN_ERROR"
  | "STORAGE"
  | "RUNTIME_UNAVAILABLE";

export interface PluginError extends Error {
  readonly code: PluginErrorCode;
}

export interface WebInkAPI {
  readonly page: {
    info(): Promise<{ url: string; title: string; kind: PageKind }>;
    setStyle(style: { textZoom: TextZoom }): Promise<void>;
    clearStyle(): Promise<void>;
  };
  readonly selection: {
    read(): Promise<{ text: string }>;
  };
  readonly storage: {
    get(key: string): Promise<JSONValue | null>;
    set(key: string, value: JSONValue): Promise<void>;
    remove(key: string): Promise<void>;
  };
  readonly network: {
    get(url: string): Promise<{ status: number; text: string }>;
  };
}

export type PluginResult = undefined | { text: string };
export type PluginMain = (commandId: string, api: WebInkAPI) => Promise<PluginResult>;

/** Assign this entry point from a classic script: globalThis.pluginMain = async (...) => ... */
declare global {
  var pluginMain: PluginMain;
}
