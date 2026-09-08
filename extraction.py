from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel

# ── Regex patterns ────────────────────────────────────────────────────────────

_CARD_RE       = re.compile(r"\b(\d[\d\s\-]{11,21}\d)\b")
_ACCOUNT_RE    = re.compile(r"\b([A-Za-z]{1,6}[\-\s]?\d{1,10})\b")
_CVV_RE        = re.compile(r"\b(\d{3,4})\b")
_PINCODE_RE    = re.compile(r"\b(\d{6})\b")
_AADHAAR_RE    = re.compile(r"\b(\d{4})\b")
_AMOUNT_RE     = re.compile(r"\b(\d{1,10}(?:\.\d{1,2})?)\b")
_EXPIRY_RE     = re.compile(r"\b(0?[1-9]|1[0-2])[\/\-](20\d{2}|\d{2})\b")
_DOB_PATTERNS  = [
    # YYYY-MM-DD or YYYY/MM/DD
    re.compile(r"\b((?:19|20)\d{2})[\/\-](0?[1-9]|1[0-2])[\/\-](0?[1-9]|[12]\d|3[01])\b"),
    # DD-MM-YYYY or DD/MM/YYYY
    re.compile(r"\b(0?[1-9]|[12]\d|3[01])[\/\-](0?[1-9]|1[0-2])[\/\-]((?:19|20)\d{2})\b"),
    # Month name formats: "14th May 1990", "May 14, 1990", "14 May 1990"
    re.compile(
        r"\b(0?[1-9]|[12]\d|3[01])(?:st|nd|rd|th)?\s+"
        r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"\s+((?:19|20)\d{2})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"\s+(0?[1-9]|[12]\d|3[01])(?:st|nd|rd|th)?,?\s+((?:19|20)\d{2})\b",
        re.IGNORECASE,
    ),
]

_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_CONFIRM_RE = re.compile(
    r"\b(yes|yeah|yep|ok|okay|sure|correct|proceed|go ahead|confirm|that'?s? right|absolutely)\b",
    re.IGNORECASE,
)
_DENY_RE = re.compile(
    r"\b(no|nope|wrong|incorrect|not right|change it|different)\b",
    re.IGNORECASE,
)
_CANCEL_RE = re.compile(
    r"\b(cancel|stop|quit|never mind|nevermind|forget it|abort)\b",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(r"\?|what\b|how\b|can you\b|tell me\b", re.IGNORECASE)
_PAY_ALL_RE  = re.compile(
    r"\b(pay it all|pay all|full(?: amount)?|whole(?: amount)?|entire(?: amount)?|clear(?: my)? balance|pay off)\b",
    re.IGNORECASE,
)


# ── Output model (same interface as before) ───────────────────────────────────

class TurnExtraction(BaseModel):
    account_id:      str | None = None
    full_name:       str | None = None
    dob:             date | None = None
    aadhaar_last4:   str | None = None
    pincode:         str | None = None
    amount:          Decimal | None = None
    cardholder_name: str | None = None
    card_number:     str | None = None
    cvv:             str | None = None
    expiry_month:    int | None = None
    expiry_year:     int | None = None
    intent: Literal[
        "provide_info", "question", "confirm", "deny", "cancel", "other"
    ] = "other"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_month_name(s: str) -> int:
    return _MONTH_MAP[s[:3].lower()]


def _two_digit_year(y: str) -> int:
    n = int(y)
    if n < 100:
        return 2000 + n if n <= 29 else 1900 + n
    return n


def _parse_dob(text: str) -> date | None:
    # YYYY-MM-DD / YYYY/MM/DD
    m = _DOB_PATTERNS[0].search(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # DD-MM-YYYY / DD/MM/YYYY
    m = _DOB_PATTERNS[1].search(text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass

    # "14th May 1990" / "14 May 1990"
    m = _DOB_PATTERNS[2].search(text)
    if m:
        try:
            return date(int(m.group(3)), _parse_month_name(m.group(2)), int(m.group(1)))
        except ValueError:
            pass

    # "May 14, 1990"
    m = _DOB_PATTERNS[3].search(text)
    if m:
        try:
            return date(int(m.group(3)), _parse_month_name(m.group(1)), int(m.group(2)))
        except ValueError:
            pass

    return None


def _parse_expiry(text: str) -> tuple[int, int] | None:
    m = _EXPIRY_RE.search(text)
    if not m:
        return None
    month = int(m.group(1))
    year  = _two_digit_year(m.group(2))
    return month, year


def _redact_card(text: str) -> tuple[str, str | None]:
    m = _CARD_RE.search(text)
    if not m:
        return text, None
    raw = re.sub(r"[\s\-]", "", m.group(1))
    redacted = text[: m.start()] + "[CARD_NUMBER]" + text[m.end():]
    return redacted, raw


def _classify_intent(text: str, has_fields: bool) -> str:
    if _CANCEL_RE.search(text):
        return "cancel"
    if _PAY_ALL_RE.search(text):
        return "confirm"
    if _CONFIRM_RE.search(text):
        return "confirm"
    if _DENY_RE.search(text):
        return "deny"
    if _QUESTION_RE.search(text):
        return "question"
    if has_fields:
        return "provide_info"
    return "other"


def _looks_like_name(text: str) -> bool:
    """True if the text looks like it could be a person's name."""
    words = text.strip().split()
    if len(words) < 2:
        return False
    return all(re.match(r"^[A-Za-z]{2,}$", w) for w in words)


# ── Public API ────────────────────────────────────────────────────────────────

def extract(
    user_text: str, history: list[dict]
) -> tuple[TurnExtraction, str | None]:
    redacted, raw_card = _redact_card(user_text)
    text = redacted.strip()

    ex = TurnExtraction()

    # Card number (regex-extracted before anything else)
    if raw_card:
        ex = ex.model_copy(update={"card_number": raw_card})

    # Account ID  — e.g. ACC001, ACC-001, acc001
    m = _ACCOUNT_RE.search(text)
    if m:
        candidate = re.sub(r"[\s\-]", "", m.group(1)).upper()
        # Must start with letters and end with digits to avoid false positives
        if re.match(r"^[A-Z]+\d+$", candidate):
            ex = ex.model_copy(update={"account_id": candidate})

    # Date of birth
    dob = _parse_dob(text)
    if dob:
        ex = ex.model_copy(update={"dob": dob})

    # Expiry (only when card context — avoid confusing with DOB)
    if raw_card or "[CARD_NUMBER]" in text:
        exp = _parse_expiry(text)
        if exp:
            ex = ex.model_copy(update={"expiry_month": exp[0], "expiry_year": exp[1]})
    else:
        # Expiry can also appear without the card number in the same message
        exp = _parse_expiry(text)
        if exp and not dob:  # don't confuse MM/YYYY with date-of-birth
            ex = ex.model_copy(update={"expiry_month": exp[0], "expiry_year": exp[1]})

    # 6-digit pincode (before aadhaar_last4 so it doesn't steal digits)
    pin_m = _PINCODE_RE.search(text)
    if pin_m and not dob:
        ex = ex.model_copy(update={"pincode": pin_m.group(1)})

    # Aadhaar last 4 — 4 isolated digits not already consumed as CVV/year/month
    if not ex.pincode:
        aam = _AADHAAR_RE.search(text)
        if aam and not dob:
            ex = ex.model_copy(update={"aadhaar_last4": aam.group(1)})

    # CVV — 3 or 4 digits that appear near "cvv" / "security code" keywords
    cvv_ctx = re.search(r"cvv[:\s]*(\d{3,4})|security\s+code[:\s]*(\d{3,4})", text, re.IGNORECASE)
    if cvv_ctx:
        cvv_val = cvv_ctx.group(1) or cvv_ctx.group(2)
        ex = ex.model_copy(update={"cvv": cvv_val})

    # Amount — a number that is NOT a year, not a pincode, not 4-digit aadhaar
    if not _PAY_ALL_RE.search(text):
        for am in _AMOUNT_RE.finditer(text):
            val_str = am.group(1)
            val = float(val_str)
            # Skip values that look like years or pin-length numbers already captured
            if 1900 <= val <= 2100:
                continue
            if ex.pincode and val_str == ex.pincode:
                continue
            try:
                ex = ex.model_copy(update={"amount": Decimal(val_str)})
                break
            except InvalidOperation:
                pass

    # Full name / cardholder name — look for "name: X" pattern or plain multi-word names
    name_kw = re.search(
        r"(?:name|cardholder)[:\s]+([A-Za-z][A-Za-z\s]{2,40})",
        text, re.IGNORECASE,
    )
    if name_kw:
        name_val = name_kw.group(1).strip()
        if raw_card:
            ex = ex.model_copy(update={"cardholder_name": name_val})
        else:
            ex = ex.model_copy(update={"full_name": name_val})
    else:
        # Heuristic: if the whole input looks like a name (2+ alpha words), use it
        clean = re.sub(r"[^A-Za-z\s]", "", text).strip()
        if _looks_like_name(clean) and not ex.account_id and not dob:
            if raw_card:
                ex = ex.model_copy(update={"cardholder_name": clean})
            else:
                ex = ex.model_copy(update={"full_name": clean})

    has_fields = any([
        ex.account_id, ex.full_name, ex.dob, ex.aadhaar_last4, ex.pincode,
        ex.amount, ex.cardholder_name, ex.card_number, ex.cvv,
        ex.expiry_month, ex.expiry_year,
    ])
    ex = ex.model_copy(update={"intent": _classify_intent(text, has_fields)})

    return ex, None
