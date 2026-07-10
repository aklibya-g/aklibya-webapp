"""
Extract ALL WhatsApp messages using Selenium
Usage: py -3.11 extract_whatsapp.py

Steps:
1. Run the script
2. Scan QR code on WhatsApp Web
3. Click on the chat you want to extract
4. Press Enter in this terminal when ready
5. Script scrolls and extracts ALL messages
6. Saves to Desktop as .txt file
"""

import time
import os
import json
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")

def main():
    print("=" * 60)
    print("  استخراج رسائل الواتساب — شركة اليمامة المالية")
    print("=" * 60)
    print()
    print("  الخطوة 1: فتح واتساب ويب...")
    print()

    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_experimental_option("excludeSwitches", ["enable-logging"])

    driver = webdriver.Chrome(options=options)
    driver.get("https://web.whatsapp.com/")

    print("  ✅ تم فتح واتساب ويب")
    print("  📱 امسح كود QR بالهاتف")
    print()

    input("  ← اضغط Enter بعد ما تفتح المحادثة المطلوبة...")

    print()
    print("  الخطوة 2: جاري تحميل جميع الرسائل (سكرول تلقائي)...")

    # Scroll to top
    try:
        main_panel = driver.find_element(By.CSS_SELECTOR, '#main > div:last-child > div:last-child')
    except:
        main_panel = None

    if not main_panel:
        try:
            main_panel = driver.find_element(By.CSS_SELECTOR, '[data-testid="conversation-panel-messages"]')
        except:
            print("  ❌ لم يتم العثور على المحادثة. تأكد إنك فاتح محادثة.")
            driver.quit()
            return

    # Scroll to top repeatedly
    scroll_count = 0
    prev_count = 0
    stable = 0

    while stable < 5 and scroll_count < 150:
        driver.execute_script("arguments[0].scrollTop = 0;", main_panel)
        time.sleep(0.3)
        scroll_count += 1

        msgs = driver.find_elements(By.CSS_SELECTOR, 'div[data-testid="msg-container"]')
        current_count = len(msgs)

        if current_count == prev_count:
            stable += 1
        else:
            stable = 0
        prev_count = current_count

        if scroll_count % 5 == 0:
            print(f"  📥 تم تحميل {current_count} رسالة... [{scroll_count}]")

    print(f"  ✅ تم تحميل {current_count} رسالة")
    print()
    print("  الخطوة 3: جاري استخراج الرسائل...")

    # Extract all messages
    msgs = driver.find_elements(By.CSS_SELECTOR, 'div[data-testid="msg-container"]')
    messages = []
    seen = set()

    for msg in msgs:
        try:
            text_el = msg.find_element(By.CSS_SELECTOR, 'span.selectable-text, span._ao3q')
            text = text_el.text.strip()
            if text and len(text) > 1 and text not in seen:
                seen.add(text)
                messages.append(text)
        except:
            pass

    if not messages:
        print("  ❌ لم يتم العثور على رسائل.")
        driver.quit()
        return

    print(f"  ✅ تم استخراج {len(messages)} رسالة")
    print()
    print("  الخطوة 4: حفظ الملف...")

    # Save to file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"واتساب_{timestamp}.txt"
    filepath = os.path.join(DESKTOP, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n\n".join(messages))

    print(f"  ✅ تم الحفظ في: {filepath}")
    print(f"  📄 {len(messages)} رسالة")
    print()
    print("  الخطوة 5: رفع الملف على المنظومة...")
    print(f"  🔗 {os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'smart-import.html')}")
    print()
    print("  أو افتح المنظومة → استيراد ذكي → رفع ملف التكست")
    print()
    print("=" * 60)
    print("  اضغط أي زر لإغلاق المتصفح...")
    print("=" * 60)

    input()

    # Also copy to clipboard
    try:
        import subprocess
        cmd = f'cmd /c "clip < {filepath}"'
        # Or use pyperclip approach
    except:
        pass

    driver.quit()
    print("  ✅ تم الإغلاق")

if __name__ == "__main__":
    main()
