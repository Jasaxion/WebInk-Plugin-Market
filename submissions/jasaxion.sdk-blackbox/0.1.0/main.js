const storageKey = "remembered-selection";

async function confirmUndeclaredPermissionIsDenied(api) {
  try {
    await api.page.info();
    throw new Error("Host allowed undeclared page.info permission");
  } catch (error) {
    if (error && error.code === "PERMISSION_DENIED") return;
    throw error;
  }
}

function utf8Length(text) {
  let bytes = 0;
  for (const character of text) {
    const codePoint = character.codePointAt(0);
    bytes += codePoint <= 0x7f ? 1 : codePoint <= 0x7ff ? 2 : codePoint <= 0xffff ? 3 : 4;
  }
  return bytes;
}

globalThis.pluginMain = async (commandId, api) => {
  await confirmUndeclaredPermissionIsDenied(api);

  if (commandId === "remember") {
    let text;
    try {
      ({ text } = await api.selection.read());
    } catch (error) {
      if (error && error.code === "CONTEXT_GONE") {
        return { text: "The selection is no longer available. Select text and try again." };
      }
      throw error;
    }

    if (utf8Length(JSON.stringify(text)) > 16 * 1024) {
      return { text: "The selection cannot be stored because its encoded value exceeds 16 KiB." };
    }
    await api.storage.set(storageKey, text);
    return { text: "Selection remembered." };
  }

  if (commandId === "recall") {
    const value = await api.storage.get(storageKey);
    if (value === null) return { text: "No selection has been remembered." };
    if (typeof value !== "string") throw new Error("Stored selection is not text");
    return { text: value };
  }

  throw new Error("Unsupported command");
};
