from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator
from tenacity import retry, stop_after_attempt, wait_random_exponential
import os

import os
from datetime import date

import requests
from dotenv import load_dotenv

load_dotenv()

baseURL = os.getenv("baseURL")

class PaymentError(Exception):
    """Base for payment processing failures."""

class PaymentDeclined(PaymentError):
    """Server rejected the payment with a known error code. User-fixable."""
    def __init__(self, error_code: str):
        self.error_code = error_code
        super().__init__(f"payment declined: {error_code}")

class PaymentUnavailable(PaymentError):
    """Could not reach the server, or got an unusable response."""

class PaymentOutcomeUnknown(PaymentError):
    """Request may or may not have been processed. Never auto-retry."""


def luhn_ok(number: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0




class Card(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cardholder_name: str
    card_number: str
    cvv: str
    expiry_month: int = Field(ge=1, le=12)
    expiry_year: int = Field(ge=2000, le=2099)

    @field_validator("card_number", "cvv", mode="before")
    @classmethod
    def strip_separators(cls, v: str) -> str:
        return "".join(str(v).split()).replace("-", "")

    @field_validator("card_number")
    @classmethod
    def check_card_number(cls, v: str) -> str:
        if not v.isdigit():
            raise ValueError("card number must contain digits only")
        if not 13 <= len(v) <= 19:
            raise ValueError("card number length is invalid")
        if not luhn_ok(v):
            raise ValueError("card number failed the checksum")
        return v

    @model_validator(mode="after")
    def check_cvv_and_expiry(self):
        # Amex takes 4 digits, everything else 3
        expected = 4 if self.card_number[:2] in ("34", "37") else 3
        if not self.cvv.isdigit() or len(self.cvv) != expected:
            raise ValueError(f"CVV must be {expected} digits for this card")

        # a card is valid through the last day of its expiry month
        today = date.today()
        if (self.expiry_year, self.expiry_month) < (today.year, today.month):
            raise ValueError("card has expired")
        return self

    # keep the PAN and CVV out of logs and tracebacks
    def __repr__(self) -> str:
        return f"Card(cardholder_name={self.cardholder_name!r}, card_number='****{self.card_number[-4:]}', cvv='***')"

    __str__ = __repr__


class PaymentMethod(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["card"] = "card"
    card: Card


class PaymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str
    amount: Decimal
    payment_method: PaymentMethod

    @field_validator("amount")
    @classmethod
    def check_amount(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("amount must be greater than zero")
        if -v.as_tuple().exponent > 2:
            raise ValueError("amount cannot have more than 2 decimal places")
        return v

    @field_serializer("amount")
    def serialize_amount(self, v: Decimal) -> float:
        return float(v)

class PaymentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    success: bool
    transaction_id: str | None = None
    error_code: str | None = None


#api call 
def process_payment(request: PaymentRequest) -> PaymentResponse:
    """Submit a card payment. Not retried — this endpoint is not idempotent."""
    try:
        r = requests.post(
            f"{baseURL}/api/process-payment",
            json=request.model_dump(mode="json"),
            timeout=15,
        )
    except requests.Timeout as e:
        raise PaymentOutcomeUnknown(
            "payment request timed out; outcome undetermined"
        ) from e
    except requests.RequestException as e:
        raise PaymentUnavailable(f"could not reach payment service: {e}") from e

    if r.status_code == 422:
        try:
            body = PaymentResponse.model_validate(r.json())
        except ValueError as e:
            raise PaymentUnavailable(f"unparseable 422 body: {e}") from e
        raise PaymentDeclined(body.error_code or "unknown_error")

    if not r.ok:
        raise PaymentUnavailable(f"payment service returned {r.status_code}")

    try:
        body = PaymentResponse.model_validate(r.json())
    except ValueError as e:
        raise PaymentUnavailable(f"unexpected response shape: {e}") from e

    if not body.success or not body.transaction_id:
        raise PaymentUnavailable("200 response without a transaction id")
    return body