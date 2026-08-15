#!/usr/bin/env python3
"""Shared, audit-grade PDF text extraction for ICICI statements.

Design notes (these exist because a prior probe produced false numbers):
  * NEVER use page.search_for() to count occurrences. It returns every rect on
    the page for a string, so calling it once per regex match double-counts
    quadratically (N matches x N rects = N^2 hits).
  * Match at WORD level so occurrences are word-bounded. ICICI narrations
    contain real tokens like "VISAKHAPATNAM" that a naive substring search for
    "VISA" hits.
  * ICICI wraps tokens mid-word ("CHURCHGA TE", "McDonald s"). So report BOTH a
    STRICT word-bounded match and a LOOSE whitespace-stripped match, and flag
    where they disagree instead of silently picking one.
"""
import re
import unicodedata

import fitz


def norm_char(c: str) -> str:
    """Fold the characters ICICI actually varies on."""
    if c in "‘’ʼ´":
        return "'"
    if c in "“”":
        return '"'
    if c in "‐‑‒–—−":
        return "-"
    return c


def page_lines(page):
    """Return lines as dicts: {text, words, bbox, page}. Words carry own bboxes.

    Lines are built from ("words") output grouped by (block, line) so a "line"
    is a real typographic line, not everything sharing a y-coordinate.
    """
    ws = page.get_text("words")  # x0,y0,x1,y1,word,block,line,word_no
    groups = {}
    for x0, y0, x1, y1, w, b, ln, wn in ws:
        groups.setdefault((b, ln), []).append((x0, y0, x1, y1, w, wn))
    lines = []
    for key in sorted(groups, key=lambda k: (min(w[1] for w in groups[k]), min(w[0] for w in groups[k]))):
        wl = sorted(groups[key], key=lambda t: t[5])
        text = " ".join("".join(norm_char(c) for c in w[4]) for w in wl)
        lines.append({
            "page": page.number + 1,
            "block": key[0],
            "line": key[1],
            "text": text,
            "words": [{"bbox": [round(w[0], 2), round(w[1], 2), round(w[2], 2), round(w[3], 2)],
                       "w": "".join(norm_char(c) for c in w[4])} for w in wl],
            "bbox": [round(min(w[0] for w in wl), 2), round(min(w[1] for w in wl), 2),
                     round(max(w[2] for w in wl), 2), round(max(w[3] for w in wl), 2)],
        })
    return lines


def doc_lines(path):
    doc = fitz.open(path)
    out = []
    for p in doc:
        out.extend(page_lines(p))
    meta = {"n_pages": doc.page_count,
            "page_rects": [[round(v, 2) for v in (p.rect.x0, p.rect.y0, p.rect.x1, p.rect.y1)] for p in doc]}
    doc.close()
    return out, meta


_WORDCH = re.compile(r"[0-9A-Za-z]")


def find_token(lines, token, loose=True):
    """Find `token` in `lines`.

    Returns list of hits: {page, bbox, mode, line, matched}.
      mode='STRICT' -> word-bounded match in the space-joined line text.
      mode='LOOSE'  -> match only visible after stripping ALL whitespace
                       (i.e. the token was wrapped mid-word by the PDF).
    Each real occurrence is emitted exactly once. No search_for().
    """
    hits = []
    tok = token.upper()
    tok_ns = re.sub(r"\s+", "", tok)
    for li, ln in enumerate(lines):
        up = ln["text"].upper()
        # --- STRICT: word-bounded occurrences in the spaced text ---
        strict_spans = []
        for m in re.finditer(re.escape(tok), up):
            s, e = m.span()
            before = up[s - 1] if s > 0 else ""
            after = up[e] if e < len(up) else ""
            if _WORDCH.match(before) or _WORDCH.match(after):
                continue  # inside a longer word, e.g. VISAKHAPATNAM
            strict_spans.append((s, e))
        for s, e in strict_spans:
            hits.append({"page": ln["page"], "line_idx": li, "mode": "STRICT",
                         "matched": ln["text"][s:e], "bbox": _span_bbox(ln, s, e),
                         "line": ln["text"]})
        if not loose:
            continue
        # --- LOOSE: only report if stripping whitespace reveals MORE hits ---
        ns = re.sub(r"\s+", "", up)
        n_loose = len(re.findall(re.escape(tok_ns), ns))
        if n_loose > len(strict_spans):
            for _ in range(n_loose - len(strict_spans)):
                hits.append({"page": ln["page"], "line_idx": li, "mode": "LOOSE",
                             "matched": token, "bbox": ln["bbox"], "line": ln["text"]})
    return hits


def _span_bbox(ln, s, e):
    """Map a char span in the space-joined line text back to a bbox."""
    pos, boxes = 0, []
    for w in ln["words"]:
        wl = len(w["w"])
        ws_, we_ = pos, pos + wl
        if ws_ < e and we_ > s:
            boxes.append(w["bbox"])
        pos = we_ + 1  # +1 for the joining space
    if not boxes:
        return ln["bbox"]
    return [round(min(b[0] for b in boxes), 2), round(min(b[1] for b in boxes), 2),
            round(max(b[2] for b in boxes), 2), round(max(b[3] for b in boxes), 2)]


NUM_RE = re.compile(r"-?\d{1,3}(?:,\d{2,3})*(?:\.\d+)?|-?\d+(?:\.\d+)?")


def parse_indian_num(s):
    """Parse an Indian-grouped number ('1,23,456.78') -> float, else None."""
    s = s.strip().replace("`", "").replace("₹", "").replace(",", "")
    s = s.replace("(", "-").replace(")", "")
    try:
        return float(s)
    except ValueError:
        return None


def numbers_in(text):
    return [(m.group(), parse_indian_num(m.group())) for m in NUM_RE.finditer(text)]
