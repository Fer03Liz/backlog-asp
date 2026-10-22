"""Números de parte, guías y soluciones de Apple."""
import re

TRACKING_RE = re.compile(r"\b([A-Z]{3,6}\d{5,})\b")          # ACSP005752, GEOPA21555
PART_RE = re.compile(r"\b[A-Z]{0,3}\d{3}-\d{4,5}\b")         # 661-43656, LZ661-45268
PREFIX_RE = re.compile(r"^[A-Z]{1,3}(?=\d{3}-)")

OPTION_SHORT = {
    "In Transit": "In Transit",
    "Miami": "Miami",
    "OH": "OH",
    "FG": "FG",
    "WUR": "WUR",
    "To be Defined": "Por definir",
    "Substitute Upgrade or/and Color change": "Upgrade",
}


def norm_part(part) -> str:
    return PREFIX_RE.sub("", (part or "").strip().upper())


def parse_tracking(*texts) -> list:
    found = []
    for t in texts:
        for tok in TRACKING_RE.findall(t or ""):
            if tok not in found:
                found.append(tok)
    return found


def parse_parts(text, exclude=None) -> list:
    out = []
    for p in PART_RE.findall(text or ""):
        if p != exclude and p not in out:
            out.append(p)
    return out
