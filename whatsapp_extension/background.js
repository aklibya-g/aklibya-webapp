chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === "openSmartImport") {
    const typeParam = message.type || "transfer";
    chrome.tabs.query(
      { url: ["*://webapp-production-5df54.up.railway.app/*", "*://127.0.0.1:8000/*"] },
      (tabs) => {
        if (tabs.length > 0) {
          chrome.tabs.update(tabs[0].id, { active: true });
          chrome.tabs.sendMessage(tabs[0].id, {
            action: "loadExtractedData",
            text: message.text,
            type: message.type,
          });
        } else {
          chrome.tabs.create({ url: `https://webapp-production-5df54.up.railway.app/smart-import/` });
        }
      }
    );
  }
  if (message.action === "getExtractedData") {
    chrome.storage.local.get(["extractedText", "extractedType"], (data) => {
      sendResponse(data);
    });
    return true;
  }
});
