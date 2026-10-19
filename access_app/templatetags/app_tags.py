import builtins
import re

from django import template

register = template.Library()

_WA_EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "☀-➿⏳⌛"
    "\U0001F3FB-\U0001F3FF"
    "]+",
    flags=re.UNICODE,
)

_CUR_FULL = {
    "ج م": "جنيه مصري",
    "د ل": "جنيه ليبي",
    "EG": "جنيه مصري",
    "EGP": "جنيه مصري",
    "LYD": "جنيه ليبي",
}


@register.filter(name="getattr")
def getattr_filter(obj, attr):
    return builtins.getattr(obj, attr, None)


@register.filter(name="fmt")
def fmt_amount(value):
    try:
        return f"{float(value):,.3f}"
    except (ValueError, TypeError):
        return value or "-"


@register.filter(name="split")
def split_filter(value, sep=","):
    if not value:
        return []
    return str(value).split(sep)


@register.filter(name="div")
def div_filter(value, arg):
    try:
        if float(arg) == 0:
            return None
        return float(value) / float(arg)
    except (ValueError, TypeError, ZeroDivisionError):
        return None


@register.filter(name="western")
def western_filter(value, decimals=0):
    try:
        return f"{float(value):,.{int(decimals)}f}"
    except (ValueError, TypeError):
        return "-"


@register.filter(name="cur_full")
def cur_full_filter(value):
    if not value:
        return ""
    return _CUR_FULL.get(str(value).strip(), value)


@register.filter(name="wa_note")
def wa_note_filter(msg):
    """Remaining note lines of a WA message after removing type/phone/amount/currency noise."""
    if msg is None:
        return ""
    text = (getattr(msg, "message_text", "") or "").strip()
    if not text:
        return ""
    phone = (getattr(msg, "phone_number", "") or "").strip()
    ttype = (getattr(msg, "transfer_type", "") or "").strip()
    cur = (getattr(msg, "currency", "") or "").strip()
    cur_full = _CUR_FULL.get(cur, cur)
    parts = []
    if phone:
        parts.append(re.escape(phone))
    if ttype:
        parts.append(re.escape(ttype))
    if cur:
        parts.append(re.escape(cur))
    if cur_full and cur_full != cur:
        parts.append(re.escape(cur_full))
    parts.extend([r"ج\.?\s?م", r"د\.?\s?ل", r"جنيه\s?مصري", r"جنيه\s?ليبي", r"LYD", r"EGP"])
    drop = re.compile("|".join(parts), re.IGNORECASE) if parts else None

    lines = []
    for raw in text.splitlines():
        line = _WA_EMOJI_RE.sub("", raw)
        line = re.sub(r"@\d{6,}", "", line)
        if drop:
            line = drop.sub(" ", line)
        line = re.sub(r"^\s*المبلغ\s*[:：]?\s*", "", line)
        line = re.sub(r"\s+", " ", line).strip(" -–—:·,،")
        if not line:
            continue
        if re.fullmatch(r"[\d.,]+", line):
            continue
        lines.append(line)
    return " ".join(lines[:3])
