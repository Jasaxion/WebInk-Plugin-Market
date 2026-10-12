globalThis.pluginMain = async (commandId, api) => {
  if (commandId === "enlarge") {
    await api.page.setStyle({ textZoom: 125 });
    return { text: "Text zoom set to 125%." };
  }
  if (commandId === "clear") {
    await api.page.clearStyle();
    return { text: "Text zoom cleared." };
  }
  throw new Error("Unsupported command");
};
