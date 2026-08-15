"""Conservative text-layer support checks; never used to overrule Opus image evidence."""

import re
from decimal import Decimal

from judge.normalization import norm_date, norm_num


def _flexible_literal(value: str) -> str:
    parts = re.split(r"\s+", value.strip())
    return r"\s+".join(re.escape(part) for part in parts if part)


def text_supports_value(pdf_text: str, value: object, kind: str = "text") -> bool:
    """Conservatively recognize wrapped text, dates and Indian-grouped numbers."""
    if value is None or not str(value).strip():
        return False
    if kind == "date":
        canonical = norm_date(value)
        if canonical is None:
            return False
        day, month, year = (int(part) for part in canonical.split("/"))
        pattern = rf"(?<!\d)0?{day}\s*[/.-]\s*0?{month}\s*[/.-]\s*{year}(?!\d)"
    elif kind == "number":
        number = norm_num(value)
        if number is None:
            return False
        raw = format(abs(Decimal(str(number))), "f")
        if "." in raw:
            raw = raw.rstrip("0").rstrip(".")
        whole, dot, fraction = raw.partition(".")
        grouped_digits = r",?".join(re.escape(digit) for digit in whole)
        fraction_pattern = rf"\s*\.\s*{re.escape(fraction)}" if dot else r"(?:\s*\.\s*0+)?"
        pattern = rf"(?<![\d,.]){grouped_digits}{fraction_pattern}(?!\d)"
    else:
        pattern = rf"(?<!\w){_flexible_literal(str(value))}(?!\w)"
    return re.search(pattern, pdf_text, flags=re.I) is not None
