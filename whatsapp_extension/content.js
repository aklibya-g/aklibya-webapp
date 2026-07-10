const SERVER_URL = "https://webapp-production-5df54.up.railway.app";

let floatingBtn = null;
let panel = null;
let selectedChat = null;

function init() {
  if (document.getElementById("yma-floating-btn")) return;
  createFloatingButton();
  observeChatChanges();
}

function createFloatingButton() {
  floatingBtn = document.createElement("div");
  floatingBtn.id = "yma-floating-btn";
  floatingBtn.innerHTML = "📦";
  floatingBtn.title = "استيراد للمنظمة";
  floatingBtn.addEventListener("click", togglePanel);
  document.body.appendChild(floatingBtn);
}

function togglePanel() {
  if (panel && panel.parentNode) {
    panel.remove();
    panel = null;
    return;
  }
  createPanel();
}

function createPanel() {
  panel = document.createElement("div");
  panel.id = "yma-panel";
  panel.innerHTML = `
    <div class="yma-header">
      <span class="yma-logo">📦</span>
      <span class="yma-title">شركة اليمامة المالية</span>
      <button class="yma-close" id="yma-close">✕</button>
    </div>
    <div class="yma-body">
      <div class="yma-info">
        <p>1. اختر المحادثة المطلوبة</p>
        <p>2. حدد الرسائل (Ctrl+A)</p>
        <p>3. اضغط زر الاستيراد</p>
      </div>
      <div class="yma-type-selector">
        <button class="yma-type-btn active" data-type="auto" id="yma-type-auto">🔍 تلقائي</button>
        <button class="yma-type-btn" data-type="transfer" id="yma-type-transfer">💸 حوالات</button>
        <button class="yma-type-btn" data-type="balance" id="yma-type-balance">💰 أرصدة</button>
      </div>
      <div class="yma-status" id="yma-status"></div>
      <div class="yma-preview" id="yma-preview"></div>
      <div class="yma-actions">
        <button class="yma-btn yma-btn-primary" id="yma-extract-btn">
          <span class="yma-icon">📥</span> استخراج الرسائل
        </button>
        <button class="yma-btn yma-btn-success" id="yma-send-btn" style="display:none">
          <span class="yma-icon">🚀</span> إرسال للمنظومة
        </button>
      </div>
      <div class="yma-footer">
        <a href="${SERVER_URL}/smart-import/" target="_blank" class="yma-link">فتح المنظومة ↗</a>
      </div>
    </div>
  `;
  document.body.appendChild(panel);

  document.getElementById("yma-close").addEventListener("click", () => {
    panel.remove();
    panel = null;
  });

  document.querySelectorAll(".yma-type-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".yma-type-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
    });
  });

  document.getElementById("yma-extract-btn").addEventListener("click", extractMessages);
  document.getElementById("yma-send-btn").addEventListener("click", sendToServer);
}

function extractMessages() {
  const statusEl = document.getElementById("yma-status");
  const previewEl = document.getElementById("yma-preview");
  const sendBtn = document.getElementById("yma-send-btn");

  statusEl.className = "yma-status yma-loading";
  statusEl.textContent = "جاري استخراج الرسائل...";

  setTimeout(() => {
    try {
      const messages = getWhatsAppMessages();
      if (!messages || messages.length === 0) {
        statusEl.className = "yma-status yma-error";
        statusEl.textContent = "لم يتم العثور على رسائل. حدد الرسائل أولاً (Ctrl+A)";
        return;
      }

      const text = messages.join("\n\n");
      const activeType = document.querySelector(".yma-type-btn.active").dataset.type;
      const detectedType = activeType === "auto" ? detectType(text) : activeType;

      statusEl.className = "yma-status yma-success";
      statusEl.textContent = `تم استخراج ${messages.length} رسالة — نوع: ${detectedType === "transfer" ? "حوالات" : "أرصدة"}`;

      previewEl.innerHTML = `
        <div class="yma-preview-header">
          <span>📄 ${messages.length} رسالة</span>
          <span class="yma-badge ${detectedType === "transfer" ? "yma-badge-transfer" : "yma-badge-balance"}">${detectedType === "transfer" ? "حوالات" : "أرصدة"}</span>
        </div>
        <div class="yma-preview-text">${escapeHtml(text).substring(0, 500)}${text.length > 500 ? "..." : ""}</div>
        <textarea class="yma-textarea" id="yma-extracted-text">${escapeHtml(text)}</textarea>
      `;

      sendBtn.style.display = "flex";
      sendBtn.dataset.type = detectedType;
      sendBtn.dataset.text = text;
      sendBtn.dataset.count = messages.length;
    } catch (e) {
      statusEl.className = "yma-status yma-error";
      statusEl.textContent = "خطأ في الاستخراج: " + e.message;
    }
  }, 300);
}

function getWhatsAppMessages() {
  const messages = [];

  // Method 1: Get messages from the chat panel
  const msgContainers = document.querySelectorAll('div[data-testid="msg-container"]');

  if (msgContainers.length > 0) {
    msgContainers.forEach((container) => {
      const textEl = container.querySelector("span.selectable-text, span._ao3q");
      if (textEl) {
        const text = textEl.innerText.trim();
        if (text && text.length > 2) {
          // Get timestamp if available
          const timeEl = container.querySelector("div[data-testid] span._ao3q:last-child, span._11JPr");
          const time = timeEl ? timeEl.innerText.trim() : "";
          // Get sender info from the message bubble
          const bubble = container.closest('.message-in, .message-out, [data-testid="bubble"]');
          const isIncoming = bubble ? bubble.classList.contains("message-in") || bubble.querySelector('[data-testid="msg-dblcheck"]') === null : false;
          
          const prefix = isIncoming ? "" : "";
          messages.push(prefix + text);
        }
      }
    });
  }

  // Method 2: Fallback - get all text from the main chat area
  if (messages.length === 0) {
    const chatArea = document.querySelector("#main > div:last-child > div:last-child");
    if (chatArea) {
      const spans = chatArea.querySelectorAll("span.selectable-text, span._ao3q");
      spans.forEach((span) => {
        const text = span.innerText.trim();
        if (text && text.length > 2) {
          messages.push(text);
        }
      });
    }
  }

  // Method 3: Try to get from the copy-able messages
  if (messages.length === 0) {
    const allSpans = document.querySelectorAll('[data-testid="conversation-panel-messages"] span');
    const seen = new Set();
    allSpans.forEach((span) => {
      const text = span.innerText.trim();
      if (text && text.length > 5 && !seen.has(text)) {
        seen.add(text);
        messages.push(text);
      }
    });
  }

  return messages;
}

function detectType(text) {
  const t = text.toLowerCase();
  let balanceScore = 0;
  let transferScore = 0;

  if (t.indexOf("جنيه") > -1) balanceScore += 3;
  if (t.indexOf("مصري") > -1) balanceScore += 2;
  if (t.indexOf("ج م") > -1) balanceScore += 3;
  if (t.indexOf("سعر") > -1) balanceScore += 2;
  if (t.indexOf("القيمة") > -1 || t.indexOf("القيمه") > -1) balanceScore += 3;

  if (t.indexOf("حوالة") > -1) transferScore += 3;
  if (t.indexOf("حواله") > -1) transferScore += 3;
  if (t.indexOf("كاش") > -1) transferScore += 2;
  if (t.indexOf("فودافون") > -1) transferScore += 3;
  if (t.indexOf("فادفون") > -1 || t.indexOf("فدفون") > -1) transferScore += 3;
  if (t.indexOf("انستا") > -1) transferScore += 2;
  if (t.indexOf("تحويل بنكي") > -1) transferScore += 3;
  if (t.indexOf("صك") > -1) transferScore += 2;
  if (/01\d{9}/.test(text)) transferScore += 2;

  if (transferScore >= 2) return "transfer";
  if (balanceScore >= 3) return "balance";
  return "transfer";
}

function sendToServer() {
  const sendBtn = document.getElementById("yma-send-btn");
  const statusEl = document.getElementById("yma-status");
  const text = sendBtn.dataset.text;
  const type = sendBtn.dataset.type;

  if (!text) return;

  statusEl.className = "yma-status yma-loading";
  statusEl.textContent = "جاري الإرسال للمنظمة...";
  sendBtn.disabled = true;

  // Save to chrome storage for the popup/smart import page to use
  chrome.storage.local.set(
    {
      extractedText: text,
      extractedType: type,
      extractedAt: new Date().toISOString(),
      extractedCount: parseInt(sendBtn.dataset.count) || 0,
    },
    () => {
      statusEl.className = "yma-status yma-success";
      statusEl.textContent = "✅ تم الحفظ! افتح المنظومة للمعاينة والتأكيد";
      sendBtn.disabled = false;
      sendBtn.style.display = "none";

      // Try to open the smart import page
      chrome.runtime.sendMessage({
        action: "openSmartImport",
        type: type,
      });
    }
  );
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function observeChatChanges() {
  const observer = new MutationObserver(() => {
    if (!document.getElementById("yma-floating-btn")) {
      createFloatingButton();
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
}

// Initialize
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
