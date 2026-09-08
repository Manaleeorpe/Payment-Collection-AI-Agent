from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel

# Matches digit sequences (with optional spaces/hyphens) long enough to be a card number.
# Card numbers are extracted BEFORE the LLM call so raw PANs never reach the upstream model.
_CARD_RE = re.compile(r"\b(\d[\d\s\-]{11,21}\d)\b")

_SYSTEM = """\
You are a data-extraction assistant for a payment-collection agent.
Extract ONLY what the user explicitly states in THIS turn. Leave everything else null.
Never invent or infer values that were not directly stated by the user.

Normalisation rules:
- account_id: uppercase, remove spaces/hyphens ("acc 1001" → "ACC1001", "acc-1002" → "ACC1002")
- full_name: preserve exact capitalisation as stated; do not alter case
- dob: emit strictly as YYYY-MM-DD. Accept all natural forms:
    "14th May 1990" → "1990-05-14"
    "May 14, 90"    → "1990-05-14"
    "14-05-1990"    → "1990-05-14"
    "14/05/1990"    → "1990-05-14"
  Two-digit year rule: 00–29 → 20xx, 30–99 → 19xx.
- expiry_year: expand two-digit years the same way (27 → 2027, 35 → 1935).
- aadhaar_last4: exactly 4 digits, no spaces.
- pincode: exactly 6 digits (Indian pincode).
- amount: convert spelled-out numbers ("a thousand rupees" → 1000, "five hundred" → 500).
  If the user says "pay full / whole / entire amount", "clear my balance", "pay it all",
  do NOT set amount — instead set intent to "confirm" and leave amount null.
- card_number: the text may contain [CARD_NUMBER] as a redaction token — do not emit
  a card_number from that token. Only emit card_number if you see the actual digits.
- cvv: digits only, strip spaces.
- intent classification:
    confirm      → "yes", "ok", "sure", "correct", "proceed", "go ahead", "that's right"
    deny         → "no", "that's wrong", "incorrect", "not right", "change it"
    cancel       → "cancel", "stop", "quit", "never mind", "forget it"
    question     → user asks something ("what is…", "how much…", "can you tell me…")
    provide_info → user is supplying one or more data fields
    other        → anything that doesn't fit the above
"""


class TurnExtraction(BaseModel):
    account_id: str | None = None
    full_name: str | None = None
    dob: date | None = None
    aadhaar_last4: str | None = None
    pincode: str | None = None
    amount: Decimal | None = None
    cardholder_name: str | None = None
    card_number: str | None = None
    cvv: str | None = None
    expiry_month: int | None = None
    expiry_year: int | None = None
    intent: Literal[
        "provide_info", "question", "confirm", "deny", "cancel", "other"
    ] = "other"


_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0).with_structured_output(
    TurnExtraction
)


def extract(
    user_text: str, history: list[dict]
) -> tuple[TurnExtraction, str | None]:
    """Return (extraction, clarification_message|None).

    Card numbers are regex-extracted before the LLM call and stitched back in
    after, so raw PANs never reach the upstream model.
    Returns a non-None clarification when the LLM call fails — caller should
    surface it instead of processing the turn normally.
    """
    redacted, raw_card = _redact_card(user_text)

    messages: list[dict] = [{"role": "system", "content": _SYSTEM}]
    messages.extend(history[-10:])  # cap to avoid token blowout
    messages.append({"role": "user", "content": redacted})

    try:
        result: TurnExtraction = _llm.invoke(messages)
        # Stitch the raw card digits back in if LLM saw only the redaction token
        if raw_card is not None and result.card_number is None:
            result = result.model_copy(update={"card_number": raw_card})
        return result, None
    except Exception as e:
        import traceback
        print(f"[extraction] LLM call failed: {e}", flush=True)
        traceback.print_exc()
        empty = TurnExtraction()
        if raw_card:
            empty = empty.model_copy(
                update={"card_number": raw_card, "intent": "provide_info"}
            )
        return empty, "Sorry, I had trouble understanding that. Could you rephrase?"


def _redact_card(text: str) -> tuple[str, str | None]:
    """Replace the first card-like digit sequence with [CARD_NUMBER].

    Returns (redacted_text, raw_digits_or_None).
    """
    m = _CARD_RE.search(text)
    if not m:
        return text, None
    raw = re.sub(r"[\s\-]", "", m.group(1))
    redacted = text[: m.start()] + "[CARD_NUMBER]" + text[m.end() :]
    return redacted, raw
