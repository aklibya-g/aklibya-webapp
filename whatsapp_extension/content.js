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
  loadLastExtraction();
}

function loadLastExtraction() {
  chrome.storage.local.get(["extractedAt", "extractedCount", "extractedType", "lastChat"], (data) => {
    if (data.extractedAt && panel) {
      const info = panel.querySelector('.yma-info');
      if (info) {
        const date = new Date(data.extractedAt);
        const timeStr = date.toLocaleTimeString('ar-EG', {hour:'2-digit', minute:'2-digit'});
        const count = data.extractedCount || '?';
        const type = data.extractedType === 'transfer' ? 'حوالات' : 'أرصدة';
        const chat = data.lastChat || '';
        info.innerHTML = `
          <div style="background:#ecfdf5;border:1px solid #a7f3d0;border-radius:8px;padding:8px 10px;margin-bottom:8px">
            <p style="margin:0;font-size:11px;color:#065f46;font-weight:600">📦 آخر استيراد: ${count} ${type} — ${timeStr}</p>
            ${chat ? '<p style="margin:2px 0 0;font-size:10px;color:#059669">المحادثة: ' + chat + '</p>' : ''}
          </div>
          <p>1. افتح المحادثة المطلوبة</p>
          <p>2. سكرول لأعلى (لو محتاج كل الرسائل)</p>
          <p>3. اضغط "استخراج الرسائل"</p>
        `;
      }
    }
  });
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
  statusEl.textContent = "جاري تحميل جميع الرسائل (سكرول تلقائي)...";

  // Save current chat name
  saveCurrentChat();

  // First, scroll to top to load ALL messages
  scrollToTopThenExtract(statusEl, previewEl, sendBtn);
}

function scrollToTopThenExtract(statusEl, previewEl, sendBtn) {
  const chatArea = document.querySelector('#main > div:last-child > div:last-child');
  if (!chatArea) {
    statusEl.className = "yma-status yma-error";
    statusEl.textContent = "لم يتم العثور على المحادثة. افتح محادثة أولاً.";
    return;
  }

  let scrollCount = 0;
  const maxScrolls = 100;
  let prevMsgCount = 0;
  let stableCount = 0;

  function scrollStep() {
    chatArea.scrollTop = 0;
    scrollCount++;

    const currentMsgCount = document.querySelectorAll('div[data-testid="msg-container"]').length;

    if (currentMsgCount === prevMsgCount) {
      stableCount++;
    } else {
      stableCount = 0;
    }
    prevMsgCount = currentMsgCount;

    statusEl.textContent = `جاري تحميل الرسائل... (${currentMsgCount} رسالة) [${scrollCount}]`;

    if (stableCount >= 5 || scrollCount >= maxScrolls) {
      // Done scrolling - now extract
      setTimeout(() => {
        doExtract(statusEl, previewEl, sendBtn);
      }, 500);
    } else {
      setTimeout(scrollStep, 400);
    }
  }

  // Start scrolling
  chatArea.scrollTop = 0;
  setTimeout(scrollStep, 300);
}

function doExtract(statusEl, previewEl, sendBtn) {
  try {
    const messages = getWhatsAppMessages();
    if (!messages || messages.length === 0) {
      statusEl.className = "yma-status yma-error";
      statusEl.textContent = "لم يتم العثور على رسائل.";
      return;
    }

    // Mark extracted messages with green checkmark
    markExtractedMessages();

    const text = messages.join("\n\n");
    const activeType = document.querySelector(".yma-type-btn.active").dataset.type;
    const detectedType = activeType === "auto" ? detectType(text) : activeType;

    statusEl.className = "yma-status yma-success";
    statusEl.textContent = `✅ تم استخراج ${messages.length} رسالة — نوع: ${detectedType === "transfer" ? "حوالات" : "أرصدة"}`;

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
}

function markExtractedMessages() {
  // Remove old marks
  document.querySelectorAll('.yma-extracted-mark').forEach(el => el.remove());

  const msgContainers = document.querySelectorAll('div[data-testid="msg-container"]');
  let count = 0;

  msgContainers.forEach((container) => {
    const textEl = container.querySelector("span.selectable-text, span._ao3q");
    if (textEl && textEl.innerText.trim().length > 1) {
      // Add green checkmark badge
      const badge = document.createElement("div");
      badge.className = "yma-extracted-mark";
      badge.innerHTML = "✅";
      badge.title = "تم استخراج هذه الرسالة";
      container.style.position = "relative";
      container.appendChild(badge);
      count++;
    }
  });

  // Auto-remove marks after 30 seconds
  setTimeout(() => {
    document.querySelectorAll('.yma-extracted-mark').forEach(el => {
      el.style.opacity = '0';
      el.style.transition = 'opacity 1s';
      setTimeout(() => el.remove(), 1000);
    });
  }, 30000);
}

function getWhatsAppMessages() {
  const messages = [];
  const seen = new Set();

  // Method 1: Get messages from msg containers
  const msgContainers = document.querySelectorAll('div[data-testid="msg-container"]');
  if (msgContainers.length > 0) {
    msgContainers.forEach((container) => {
      const textEl = container.querySelector("span.selectable-text, span._ao3q");
      if (textEl) {
        const text = textEl.innerText.trim();
        if (text && text.length > 1 && !seen.has(text)) {
          seen.add(text);
          messages.push(text);
        }
      }
    });
  }

  // Method 2: Get from message bubbles directly
  if (messages.length === 0) {
    const bubbles = document.querySelectorAll('[data-testid="bubble"]');
    bubbles.forEach((bubble) => {
      const textEl = bubble.querySelector("span.selectable-text, span._ao3q");
      if (textEl) {
        const text = textEl.innerText.trim();
        if (text && text.length > 1 && !seen.has(text)) {
          seen.add(text);
          messages.push(text);
        }
      }
    });
  }

  // Method 3: Get from conversation panel
  if (messages.length === 0) {
    const panel = document.querySelector('[data-testid="conversation-panel-messages"]');
    if (panel) {
      const spans = panel.querySelectorAll("span.selectable-text, span._ao3q");
      spans.forEach((span) => {
        const text = span.innerText.trim();
        if (text && text.length > 1 && !seen.has(text)) {
          seen.add(text);
          messages.push(text);
        }
      });
    }
  }

  // Method 4: Broad fallback - get all innerText from main area
  if (messages.length === 0) {
    const mainArea = document.querySelector("#main");
    if (mainArea) {
      const spans = mainArea.querySelectorAll("span");
      spans.forEach((span) => {
        const text = span.innerText.trim();
        if (text && text.length > 5 && !seen.has(text) && !/^\d{1,2}:\d{2}$/.test(text)) {
          seen.add(text);
          messages.push(text);
        }
      });
    }
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

  // Save to chrome storage
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

// Save chat name on chat open
function saveCurrentChat() {
  const chatTitle = document.querySelector('#main header span[title]');
  if (chatTitle) {
    chrome.storage.local.set({ lastChat: chatTitle.getAttribute('title') });
  }
}

// Initialize
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
