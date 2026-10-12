globalThis.pluginMain = async (commandId, api) => {
  if (commandId !== "lookup") throw new Error("Unsupported command");
  const { text } = await api.selection.read();
  const term = Array.from(text.trim()).slice(0, 100).join("");
  if (!term) return { text: "Select a term to look up." };
  const url = "https://en.wiktionary.org/w/api.php?action=query&format=json&origin=*&prop=extracts&exintro=1&explaintext=1&redirects=1&titles=" + encodeURIComponent(term);
  const response = await api.network.get(url);
  if (response.status !== 200) throw new Error(`Wiktionary returned HTTP ${response.status}`);
  const data = JSON.parse(response.text);
  const page = Object.values(data.query.pages)[0];
  const summary = page.extract || `No entry found for “${term}”.`;
  let result = "";
  let bytes = 0;
  for (const character of summary) {
    const codePoint = character.codePointAt(0);
    const size = codePoint <= 0x7f ? 1 : codePoint <= 0x7ff ? 2 : codePoint <= 0xffff ? 3 : 4;
    if (bytes + size > 8192) break;
    result += character;
    bytes += size;
  }
  return { text: result };
};
