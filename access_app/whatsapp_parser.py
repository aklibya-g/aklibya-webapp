import re

TRANSFER_TYPES = ["كاش", "فودافون كاش", "فادفون كاش", "فدفون كاش", "انستا باي", "تحويل بنكي", "صك"]
RATE_KEYWORDS = ["سعر", "السعر", "سعر الصرف", "سعرالصرف"]
KNOWN_OFFICES = ["الزاجل", "جنزور", "الجروشي"]

ARABIC_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789')


def detect_beneficiary_office(lines, raw_lines=None):
    """Read the first office line marked by a star, without guessing.

    Exports use both a standalone ``*`` line and a marker directly before the
    value (``*اوية``).  WhatsApp field labels such as ``*الهاتف*`` are not
    office markers and are skipped.
    """
    source = raw_lines or lines

    # WhatsApp forwards commonly export the first line as, for example,
    # "فودافون الزاجل" or "فودافون الجروشي".  The decorative star/diamond
    # symbols are removed by clean_emoji, so read the office suffix from this
    # first payment-method line.  "فودافون كاش" has no office and therefore
    # deliberately falls through to the العربي default.
    for raw_line in source:
        first_line = clean_emoji(str(raw_line or "").strip())
        method_match = re.match(
            r"^(فودافون|فادفون|فدفون|انستا\s*باي|تحويل\s*بنكي|كاش|صك)\s*(.*)$",
            first_line,
        )
        if not method_match:
            continue
        suffix = method_match.group(2).strip()
        suffix = re.sub(r"^كاش\s*", "", suffix).strip()
        if suffix and re.search(r"[\u0600-\u06ffA-Za-z]", suffix):
            return suffix, False
        return "العربي", False

    for index, raw_line in enumerate(source):
        marker = str(raw_line or "").strip()
        marker_match = re.match(r"^\*+\s*(.*?)\s*$", marker)
        if not marker_match:
            continue
        inline = marker_match.group(1).strip().strip("*").strip()
        field_label = re.match(r"^(الدوله|الهاتف|القيمه|المبلغ|الاسم|رقم|نوع|السعر)\b", inline)
        if inline and not field_label:
            candidate = inline
        elif field_label:
            continue
        else:
            candidate = ""
            for following in source[index + 1:]:
                candidate = str(following or "").strip()
                if candidate:
                    break
        if not candidate:
            return "", True
        cleaned = clean_emoji(candidate)
        unreadable = (
            not re.search(r"[\u0600-\u06ffA-Za-z]", cleaned)
            or is_header_line(cleaned)
            or is_phone_line(cleaned)
            or _is_rate_line(cleaned)
            or extract_egp_from_line(cleaned)
            or any(kind in cleaned for kind in TRANSFER_TYPES)
            or _is_name_line(cleaned)
        )
        return ("", True) if unreadable else (candidate, False)
    return "العربي", False


def clean_emoji(text):
    text = text.translate(ARABIC_DIGITS)
    text = re.sub(r"[\U0001F1E6-\U0001F1FF]", "", text)
    text = re.sub(r"[\U0001F300-\U0010FFFF\u2600-\u27BF\uFE00-\uFE0F]", "", text)
    text = re.sub(r"[\u200B-\u200F\u2028-\u202F\u2060-\u2069\u2066-\u2069]", "", text)
    text = re.sub(r"[^\w\s\d،.,:/\-\+\(\)\[\]\n]", "", text)
    return text.strip()


def is_phone_line(text):
    m = re.search(r'(?<!\d)(01\d{9})(?!\d)', text)
    if m:
        return m.group(1)
    m = re.search(r'(?<!\d)(00\d{10,})(?!\d)', text)
    if m:
        return m.group(1)
    return None


def is_header_line(line):
    # WhatsApp exports may use either [date, time] or date, time without
    # brackets.  The latter is the format used by recent Arabic exports.
    return bool(re.match(r"^\[.*\d{1,2}:\d{2}|^\d{1,2}[/-]\d{1,2}[/-]\d{2,4},?\s*\d{1,2}:\d{2}", line))


def _parse_number(raw_str):
    raw_str = raw_str.replace(",", "").replace(".", "")
    raw_str = raw_str.replace("،", "")
    try:
        return float(raw_str)
    except ValueError:
        return None


def extract_egp_from_line(line):
    line_norm = line.replace("،", ",")

    m = re.search(r'القيمو?ه?ة?[:\s]+(\-?\d[\d,\.]*)', line_norm)
    if m:
        v = _parse_number(m.group(1))
        if v and abs(v) >= 50:
            return abs(v)

    m = re.search(r'(\d[\d,\.]*)\s*جنيه\s*مصري', line_norm)
    if m:
        v = _parse_number(m.group(1))
        if v and v >= 50:
            return v

    m = re.search(r'(\d[\d,\.]*)\s*ج\s*م', line_norm)
    if m:
        v = _parse_number(m.group(1))
        if v and v >= 50:
            return v

    m = re.search(r'(\d[\d,\.]*)\s*ج(?!\s*م)', line_norm)
    if m:
        v = _parse_number(m.group(1))
        if v and v >= 50:
            return v

    m = re.search(r'حول\s+له?\s+(\d[\d,\.]*)', line_norm)
    if m:
        v = _parse_number(m.group(1))
        if v and v >= 50:
            return v

    m = re.search(r'(\d[\d,\.]*)\s*مصري', line_norm)
    if m:
        v = _parse_number(m.group(1))
        if v and v >= 50:
            return v

    m = re.search(r'(\d[\d,\.]*)\s*ج\.م', line_norm)
    if m:
        v = _parse_number(m.group(1))
        if v and v >= 50:
            return v

    m = re.match(r'^\s*(\d[\d,\.]*)\s*$', line_norm)
    if m:
        raw = m.group(1).replace(",", "").replace("،", "")
        # A bare numeric amount is limited to normal money-sized values;
        # longer values are usually phone/account numbers.
        if not raw.startswith("0") and 3 <= len(raw) <= 8:
            v = _parse_number(m.group(1))
            if v and v >= 50:
                return v

    m = re.search(r'(\d[\d,\.]*)\s*ألف', line_norm)
    if m:
        v = _parse_number(m.group(1))
        if v and v >= 10:
            return v

    return None


def extract_rate_from_line(line):
    if re.search(r'\d{1,2}:\d{2}\s*(pm|am)', line, re.IGNORECASE):
        if not re.search(r'سعر', line):
            return None
    if re.search(r'\[\d{2}/\d{2}', line):
        if not re.search(r'سعر', line):
            return None
    line_norm = line.replace("\u060c", ".")

    m = re.search(r'سعر[:\s]*(\d+\.?\d*)', line_norm)
    if m:
        try:
            v = float(m.group(1))
            if 3 < v < 10:
                return v
        except ValueError:
            pass

    m = re.search(r'(\d+)\s*[,.،]\s*(\d+)', line_norm)
    if m:
        r_str = m.group(1) + "." + m.group(2)
        try:
            v = float(r_str)
            if 3 < v < 10:
                return v
        except ValueError:
            pass

    m = re.search(r'(\d)\.(\d{1,2})', line_norm)
    if m:
        try:
            v = float(m.group(1) + "." + m.group(2))
            if 3 < v < 10:
                return v
        except ValueError:
            pass

    m = re.search(r'(\d{3})', line_norm)
    if m:
        digits = m.group(1)
        try:
            v = float(digits[0] + "." + digits[1:])
            if 3 < v < 10:
                return v
        except ValueError:
            pass

    m = re.search(r'(\d+\.?\d*)\s*[\U0001f1e6-\U0001f1ff]{2}', line_norm)
    if m:
        try:
            v = float(m.group(1))
            if 3 < v < 10:
                return v
        except ValueError:
            pass

    m = re.search(r'^(\d+\.?\d*)$', line_norm.strip())
    if m:
        try:
            v = float(m.group(1))
            if 3 < v < 10:
                return v
        except ValueError:
            pass

    return None


def _is_rate_line(line):
    if any(kw in line for kw in RATE_KEYWORDS):
        return True
    cleaned = line.replace("،", ",").replace(",", ".").replace(" ", "")
    try:
        v = float(cleaned)
        if 3 < v < 10:
            return True
    except ValueError:
        pass
    return False


def _is_phone_line_strict(line):
    digits = re.sub(r"[^\d]", "", line)
    if len(digits) >= 10 and (digits.startswith("01") or digits.startswith("00")):
        return True
    return False


def _is_name_line(line):
    keywords = ["بدون اسم", "الاسم", "الأسم", "اسمه", "اسم"]
    for kw in keywords:
        if kw in line:
            return True
    return False


def _is_location_line(line):
    keywords = ["الزاجل", "المتخصص", "الفرجاني", "صك", "حوله"]
    for kw in keywords:
        if kw in line:
            return True
    if re.search(r'^مصر$', line.strip()):
        return True
    return False


def parse_whatsapp_block(block, block_raw=None):
    lines = [l.strip() for l in block.strip().split("\n") if l.strip()]
    raw_lines = [l.strip() for l in block_raw.strip().split("\n")] if block_raw else lines
    if len(lines) < 1:
        return None

    phone = None
    transfer_type = None
    rate = None
    amount_egp = None
    beneficiary_office, office_unrecognized = detect_beneficiary_office(lines, raw_lines)

    for idx, line in enumerate(lines):
        orig = raw_lines[idx] if idx < len(raw_lines) else line

        if _is_name_line(line) or _is_location_line(line):
            continue

        if is_header_line(line):
            egp = extract_egp_from_line(line)
            if egp and amount_egp is None:
                amount_egp = egp
            if amount_egp is None:
                m = re.search(r'(\d{3,8})\s*[\U0001f1e6-\U0001f1ff]{2}', orig)
                if m:
                    raw = m.group(1).replace(",", "")
                    try:
                        v = float(raw)
                        if v >= 50:
                            amount_egp = v
                    except ValueError:
                        pass
            if amount_egp is None:
                m = re.search(r':\s*(\d{3,8})\s+\S', orig)
                if m:
                    raw = m.group(1)
                    if not raw.startswith("00") and not raw.startswith("01"):
                        try:
                            v = float(raw)
                            if v >= 50 and len(raw) <= 7:
                                amount_egp = v
                        except ValueError:
                            pass
            rate_from_header = extract_rate_from_line(line)
            if rate_from_header and rate is None:
                rate = rate_from_header
            if not phone:
                phone_match = re.search(r'(\+?\d[\d\s\-]{8,}\d)', orig)
                if phone_match:
                    phone = re.sub(r'[\s\-]', '', phone_match.group(1))
                    if len(phone) < 10:
                        phone = None
            if not phone:
                ph = is_phone_line(line)
                if ph:
                    phone = ph
            continue

        p = is_phone_line(line)
        if not p:
            labelled = re.sub(r"^(الرقم|رقم| phone|tel|telephone|رقم الهاتف)\s*[:：]?\s*", "", line, flags=re.IGNORECASE)
            if labelled != line:
                p = is_phone_line(labelled)
        if not p:
            header_match = re.match(r"^.*?:\s*(\d{10,})", line)
            if header_match:
                p = header_match.group(1)
        if not p:
            embedded = re.search(r'(?<!\d)(01\d{9})(?!\d)', line)
            if embedded:
                p = embedded.group(1)
        if p and not phone:
            phone = p
            if amount_egp is None:
                egp = extract_egp_from_line(line)
                if egp:
                    amount_egp = egp
                else:
                    remaining = re.sub(r'(?<!\d)(?:00\d{10,}|01\d{9})(?!\d)', '', line)
                    remaining = re.sub(r'\+?\d[\d\s-]{8,}\d', '', remaining)
                    remaining = re.sub(r'\b(تحويل|فودافون\s*كاش|كاش|فات|م)\b', '', remaining)
                    remaining = remaining.replace(",", "").replace("،", "")
                    nums = re.findall(r'(\d{2,8})', remaining)
                    for n in nums:
                        try:
                            v = float(n)
                            if v >= 50:
                                amount_egp = v
                                break
                        except ValueError:
                            pass
            continue

        if not transfer_type:
            for tt in TRANSFER_TYPES:
                if tt in line:
                    transfer_type = tt
                    break
            if transfer_type:
                continue

        egp = extract_egp_from_line(line)
        if egp and amount_egp is None:
            amount_egp = egp

        if _is_rate_line(line):
            rate_val = extract_rate_from_line(line)
            if rate_val:
                rate = rate_val
                continue
            line_clean = line.replace("،", ",").replace(",", ".").replace(" ", "")
            try:
                v = float(line_clean)
                if 3 < v < 10:
                    rate = v
                    continue
            except ValueError:
                pass

    if amount_egp is None:
        for line in lines:
            if is_header_line(line):
                continue
            if _is_rate_line(line):
                continue
            egp = extract_egp_from_line(line)
            if egp:
                amount_egp = egp
                break

    if amount_egp is None:
        for line in lines:
            if is_header_line(line):
                continue
            if _is_rate_line(line):
                continue
            if is_phone_line(line):
                continue
            stripped = re.sub(r"^(الرقم|رقم)\s*[:：]?\s*", "", line, flags=re.IGNORECASE)
            if is_phone_line(stripped):
                continue
            if re.match(r'^\+?\d', line) and len(re.sub(r'[^\d]', '', line)) >= 10:
                continue
            line_no_comma = line.replace(",", "").replace("،", "")
            # Long numeric lines are account/phone numbers, not transfer
            # amounts.  Do not inspect their 3-8 digit substrings.
            if len(re.sub(r'[^\d]', '', line_no_comma)) >= 10:
                continue
            for m in re.finditer(r'(\d{3,8})', line_no_comma):
                raw = m.group(1)
                if raw.startswith("00") or raw.startswith("01"):
                    continue
                v = float(raw)
                if v >= 50:
                    amount_egp = v
                    break
            if amount_egp:
                break

    if rate is None:
        for line in lines:
            rate_val = extract_rate_from_line(line)
            if rate_val:
                rate = rate_val
                break

    if rate is None:
        for line in lines:
            if is_header_line(line):
                continue
            line_clean = line.replace("،", ",").replace(",", ".").replace(" ", "")
            try:
                v = float(line_clean)
                if 3 < v < 10:
                    rate = v
                    break
            except ValueError:
                pass

    if not amount_egp:
        return None

    if not rate or rate <= 0:
        if amount_egp and phone:
            return {
                "receiver_tele": phone or "",
                "transfer_type": transfer_type or "كاش",
                "amount_egp": amount_egp,
                "exchange_rate": 0,
                "amount_lyd": 0,
                "office_name": beneficiary_office,
                "office_unrecognized": office_unrecognized,
            }
        return None

    return {
        "receiver_tele": phone or "",
        "transfer_type": transfer_type or "كاش",
        "amount_egp": amount_egp,
        "exchange_rate": rate,
        "amount_lyd": round(amount_egp / rate, 2),
        "office_name": beneficiary_office,
        "office_unrecognized": office_unrecognized,
    }


def _is_transfer_start(cleaned_line, prev_cleaned_line):
    if re.search(r'فودافون\s*كاش|فادفون\s*كاش', cleaned_line):
        if prev_cleaned_line and re.search(r'^\d+\s*(ج|مصري)', prev_cleaned_line):
            return False
        if prev_cleaned_line and re.search(r'القيمة|^\d{3,8}$', prev_cleaned_line):
            return False
        return True
    if re.match(r'^0\d{9,10}$', cleaned_line.replace(" ", "")):
        if prev_cleaned_line and re.search(r'رقم\s*الهاتف', prev_cleaned_line):
            return False
        if prev_cleaned_line and _is_rate_line(prev_cleaned_line):
            return True
        if prev_cleaned_line and re.search(r'♻|5[،,]\d{2}', prev_cleaned_line):
            return True
        if prev_cleaned_line and re.search(r'^\d+\s*(ج|مصري)', prev_cleaned_line):
            return True
        return True
    if re.match(r'^\+?\d[\d\s\-]{8,}\d$', cleaned_line):
        return True
    if re.search(r'انستا\s*باي', cleaned_line):
        if prev_cleaned_line and (extract_egp_from_line(prev_cleaned_line) or re.search(r'^0\d', prev_cleaned_line)):
            return False
        return True
    if re.search(r'^(لاس|الرق|مصري\s*/)', cleaned_line):
        return True
    if re.search(r'^\d+\s*(ج|مصري)', cleaned_line):
        if prev_cleaned_line and _is_rate_line(prev_cleaned_line):
            return True
        if prev_cleaned_line and re.search(r'5[،,]\d{2}$', prev_cleaned_line):
            return True
    if re.search(r'حول\s*له|حوله', cleaned_line):
        return True
    if re.search(r'صك', cleaned_line):
        if prev_cleaned_line and re.search(r'مصر|حوله', prev_cleaned_line):
            return False
        return True
    if re.search(r'بدون\s*اسم', cleaned_line):
        return True
    if _is_phone_line_strict(cleaned_line):
        if prev_cleaned_line and _is_rate_line(prev_cleaned_line):
            return True
        if prev_cleaned_line and re.search(r'♻|5[،,]\d{2}', prev_cleaned_line):
            return True
    return False


def parse_whatsapp_text(text):
    raw_lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    cleaned = [clean_emoji(l) for l in raw_lines]
    has_headers = any(is_header_line(cl) for cl in cleaned)
    blocks_raw = []
    blocks_clean = []
    current_raw = []
    current_clean = []
    for i, line in enumerate(cleaned):
        is_hdr = is_header_line(line)
        should_split = False
        if is_hdr:
            should_split = bool(current_clean)
        elif not has_headers and len(current_clean) > 0:
            prev = current_clean[-1]
            # The first line may be "فودافون الزاجل" and the next line the
            # recipient phone. Keep them in one block so office extraction
            # sees both lines together.
            prev_is_method_office = bool(re.match(
                r"^(فودافون|فادفون|فدفون|انستا\s*باي|تحويل\s*بنكي|كاش|صك)\s+.+$",
                prev,
            ))
            if _is_transfer_start(line, prev) and not prev_is_method_office:
                should_split = True
        if should_split:
            blocks_raw.append("\n".join(current_raw))
            blocks_clean.append("\n".join(current_clean))
            current_raw = []
            current_clean = []
        current_raw.append(raw_lines[i])
        current_clean.append(line)
    if current_clean:
        blocks_raw.append("\n".join(current_raw))
        blocks_clean.append("\n".join(current_clean))
    results = []
    for i in range(len(blocks_clean)):
        parsed = parse_whatsapp_block(blocks_clean[i], blocks_raw[i])
        if parsed:
            results.append(parsed)
    return results
