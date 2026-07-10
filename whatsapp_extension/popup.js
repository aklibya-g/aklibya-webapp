const SERVER_URL = "https://webapp-production-5df54.up.railway.app";

document.addEventListener("DOMContentLoaded", () => {
  loadSavedData();

  document.getElementById("openWaBtn").addEventListener("click", () => {
    chrome.tabs.create({ url: "https://web.whatsapp.com/" });
    window.close();
  });

  document.getElementById("sendBtn").addEventListener("click", sendToServer);
  document.getElementById("clearBtn").addEventListener("click", clearData);
});

function loadSavedData() {
  chrome.storage.local.get(["extractedText", "extractedType", "extractedAt", "extractedCount"], (data) => {
    if (data.extractedText) {
      const statusBox = document.getElementById("statusBox");
      const dataPreview = document.getElementById("dataPreview");
      const sendBtn = document.getElementById("sendBtn");

      statusBox.className = "status-box status-success";
      statusBox.textContent = `✅ ${data.extractedCount || 0} رسالة جاهزة — ${data.extractedType === "transfer" ? "حوالات" : "أرصدة"}`;

      const badge = document.getElementById("dataBadge");
      badge.className = `badge ${data.extractedType === "transfer" ? "badge-transfer" : "badge-balance"}`;
      badge.textContent = data.extractedType === "transfer" ? "حوالات" : "أرصدة";

      document.getElementById("dataPreviewText").textContent = data.extractedText.substring(0, 300) + (data.extractedText.length > 300 ? "..." : "");

      dataPreview.style.display = "block";
      sendBtn.disabled = false;
    }
  });
}

function sendToServer() {
  const statusBox = document.getElementById("statusBox");
  const sendBtn = document.getElementById("sendBtn");

  chrome.storage.local.get(["extractedText", "extractedType", "extractedCount"], (data) => {
    if (!data.extractedText) {
      statusBox.className = "status-box status-error";
      statusBox.textContent = "❌ لا توجد بيانات محفوظة";
      return;
    }

    statusBox.className = "status-box status-loading";
    statusBox.textContent = "جاري الإرسال...";
    sendBtn.disabled = true;

    // Try to find the tab with our system
    chrome.tabs.query({ url: ["*://webapp-production-5df54.up.railway.app/*", "*://127.0.0.1:8000/*"] }, (tabs) => {
      if (tabs.length > 0) {
        // Send to existing tab
        chrome.tabs.sendMessage(tabs[0].id, {
          action: "loadExtractedData",
          text: data.extractedText,
          type: data.extractedType,
          count: data.extractedCount,
        });
        chrome.tabs.update(tabs[0].id, { active: true });

        statusBox.className = "status-box status-success";
        statusBox.textContent = "✅ تم الإرسال! راجع المعاينة";
        sendBtn.disabled = false;
      } else {
        // Open the smart import page
        const typeParam = data.extractedType || "transfer";
        chrome.tabs.create({ url: `${SERVER_URL}/smart-import/` });

        statusBox.className = "status-box status-success";
        statusBox.textContent = "✅ تم فتح المنظومة — الصق البيانات يدوياً";
        sendBtn.disabled = false;
      }
    });
  });
}

function clearData() {
  chrome.storage.local.remove(["extractedText", "extractedType", "extractedAt", "extractedCount"], () => {
    document.getElementById("statusBox").className = "status-box status-ready";
    document.getElementById("statusBox").textContent = "جاهز للاستخدام";
    document.getElementById("dataPreview").style.display = "none";
    document.getElementById("sendBtn").disabled = true;
  });
}
