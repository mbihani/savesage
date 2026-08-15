"""Canonical value normalization ported from the repository scoring harness."""

import re
import unicodedata

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
)}


def norm_date(value: object) -> str | None:
    """Return DD/MM/YYYY, following the mature HDFC scorer."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"\s*\|\s*\d{1,2}:\d{2}(?::\d{2})?\s*(?:[AaPp]\.?[Mm]\.?)?\s*$", "", text).strip()
    text = re.sub(r"[,\s]+\d{1,2}:\d{2}(?::\d{2})?\s*(?:[AaPp]\.?[Mm]\.?)?\s*$", "", text).strip()
    text = re.sub(r"\s+\d{2}:\d{2}.*$", "", text).strip().rstrip("|").strip()
    match = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", text)
    if match:
        day, month, year = map(int, match.groups())
        return f"{day:02d}/{month:02d}/{year}" if 1 <= month <= 12 else None
    match = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$", text)
    if match:
        year, month, day = match.groups()
        return f"{int(day):02d}/{int(month):02d}/{year}"
    match = re.match(r"^(\d{1,2})\s+([A-Za-z]{3,})\.?\s*,?\s*(\d{4})$", text)
    if match:
        month = _MONTHS.get(match.group(2)[:3].lower())
        return f"{int(match.group(1)):02d}/{month:02d}/{match.group(3)}" if month else None
    match = re.match(r"^([A-Za-z]{3,})\.?\s+(\d{1,2})\s*,?\s*(\d{4})$", text)
    if match:
        month = _MONTHS.get(match.group(1)[:3].lower())
        return f"{int(match.group(2)):02d}/{month:02d}/{match.group(3)}" if month else None
    return None


def norm_num(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[,\s₹]|(?:INR)|(?:Rs\.?)", "", str(value), flags=re.I)
    negative = text.endswith("-") or (text.startswith("(") and text.endswith(")"))
    text = re.sub(r"(?i)\s*(cr|dr)$", "", text.strip("()-"))
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def norm_desc(value: object) -> str | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip()
    return re.sub(r"\s+", " ", text).lower() if text else None


def norm_last_four(value: object) -> str | None:
    if value is None:
        return None
    digits = re.sub(r"[^0-9]", "", str(value))
    return digits[-4:] if digits else None
