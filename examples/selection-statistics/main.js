globalThis.pluginMain = async (commandId, api) => {
  if (commandId !== "count") throw new Error("Unsupported command");
  try {
    const { text } = await api.selection.read();
    const characters = Array.from(text).length;
    const words = (text.match(/[\p{L}\p{N}]+(?:['’][\p{L}\p{N}]+)*/gu) || []).length;
    return { text: `${characters} characters, ${words} words` };
  } catch (error) {
    if (error && error.code === "PERMISSION_DENIED") {
      return { text: "Selection access was denied." };
    }
    throw error;
  }
};
