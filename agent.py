from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import Enum, auto
from typing import Any

from pydantic import ValidationError

from VerifyUser import (
    Account,
    ClaimedIdentity,
    AccountNotFound,
    LookupUnavailable,
    lookup_account,
    verify_user,
)
from ProcessPayment import (
    Card,
    PaymentMethod,
    PaymentRequest,
    PaymentDeclined,
    PaymentOutcomeUnknown,
    PaymentUnavailable,
    process_payment,
)
from extraction import TurnExtraction, extract

MAX_VERIFY_ATTEMPTS = 3


class State(Enum): #choice
    COLLECTING_ACCOUNT_ID = auto()
    VERIFYING = auto()
    COLLECTING_AMOUNT = auto()
    COLLECTING_CARD = auto()
    CONFIRMING = auto()
    COMPLETE = auto()
    CLOSED = auto()

MSG: dict[str, str] = {
    "greet":            "Welcome! Please provide your account ID to get started.",
    "ask_id":           "Please provide your account ID.",
    "id_not_found":     "I couldn't find an account with that ID. Please check and try again.",
    "lookup_fail":      "We're having trouble reaching our systems. Please try again in a moment.",
    "ask_identity":     (
        "Please provide your full name and one of: date of birth (YYYY-MM-DD), "
        "last 4 digits of Aadhaar, or 6-digit pincode."
    ),
    "ask_secondary":    (
        "Please also provide one of: date of birth (YYYY-MM-DD), "
        "last 4 digits of Aadhaar, or 6-digit pincode."
    ),
    "verify_fail":      (
        "I couldn't verify your identity. Please try again with your "
        "full name and a secondary factor."
    ),
    "verify_locked":    "Too many failed verification attempts. This session is now closed.",
    "ask_amount":       "How much would you like to pay? Your current balance is ₹{balance:.2f}.",
    "balance_zero":     "Your account balance is ₹0.00. There is nothing to pay.",
    "amount_invalid":   (
        "Please enter a valid amount: greater than zero, "
        "no more than ₹{balance:.2f}, with at most 2 decimal places."
    ),
    "ask_card":         (
        "Please provide your card details: cardholder name, card number, "
        "CVV, and expiry date (MM/YYYY)."
    ),
    "ask_cvv":          "The CVV doesn't match. Please re-enter the CVV for the card ending {last4}.",
    "card_expired":     "That card has expired. Please provide a different card.",
    "card_luhn":        "That card number doesn't look right. Please check and re-enter.",
    "card_invalid":     "There's an issue with the card details. Please re-enter your card information.",
    "confirm_payment":  "Please confirm: Pay ₹{amount:.2f} using the card ending {last4}? (yes / no)",
    "payment_ok":       "Payment of ₹{amount:.2f} successful. Transaction ID: {txn_id}. Thank you!",
    "payment_declined": "Payment was declined ({code}). Please try again with corrected details.",
    "payment_unknown":  (
        "We couldn't confirm whether your payment went through. "
        "Please check your statement before retrying. This session is now closed."
    ),
    "payment_fail":     "The payment service is temporarily unavailable. Please try again shortly.",
    "cancelled":        "Session cancelled. Goodbye.",
    "closed":           "This session is closed. Please start a new conversation to try again.",
    "complete":         "This session is already complete. Please start a new conversation.",
    "no_data":          "I can't share account information during this process.",
    "not_understood":   "I didn't catch that. Could you please rephrase?",
}


class Agent:
    def __init__(self) -> None:
        self.state = State.COLLECTING_ACCOUNT_ID
        self.history: list[dict] = []
        self.account: Account | None = None
        self.claimed = ClaimedIdentity()
        self.verify_attempts = 0
        self.amount: Decimal | None = None
        self.card: Card | None = None
        self._partial_card: dict[str, Any] = {}

    def next(self, user_input: str) -> dict:
        """Returns {"message": str}. Maintains all state internally."""
        if self.state == State.COMPLETE:
            return {"message": MSG["complete"]}
        if self.state == State.CLOSED:
            return {"message": MSG["closed"]}

        extraction, clarification = extract(user_input, self.history)
        self.history.append({"role": "user", "content": user_input})

        if extraction.intent == "cancel":
            reply = self._close(MSG["cancelled"])
            self.history.append({"role": "assistant", "content": reply})
            return {"message": reply}

        self._merge(extraction)
        reply = self._dispatch(extraction)

        # Only use the clarification message if dispatch fell through to not_understood
        if clarification and reply == MSG["not_understood"]:
            reply = clarification

        self.history.append({"role": "assistant", "content": reply})
        return {"message": reply}

    # ── Merge non-None extracted fields into running state ───────────────────

    def _merge(self, ex: TurnExtraction) -> None:
        # Identity fields — accumulate across turns for out-of-order support
        if ex.full_name:
            self.claimed = self.claimed.model_copy(update={"full_name": ex.full_name})
        if ex.dob is not None:
            self.claimed = self.claimed.model_copy(update={"dob": ex.dob})
        if ex.aadhaar_last4:
            self.claimed = self.claimed.model_copy(update={"aadhaar_last4": ex.aadhaar_last4})
        if ex.pincode:
            self.claimed = self.claimed.model_copy(update={"pincode": ex.pincode})

        # Amount — only merge when it belongs to the current stage
        if ex.amount is not None and self.state in (
            State.COLLECTING_AMOUNT, State.CONFIRMING
        ):
            self.amount = ex.amount

        # Card fields — only accumulate during card-collection stages
        if self.state in (State.COLLECTING_CARD, State.CONFIRMING):
            if ex.cardholder_name:
                self._partial_card["cardholder_name"] = ex.cardholder_name
            if ex.card_number:
                self._partial_card["card_number"] = ex.card_number
            if ex.cvv:
                self._partial_card["cvv"] = ex.cvv
            if ex.expiry_month:
                self._partial_card["expiry_month"] = ex.expiry_month
            if ex.expiry_year:
                self._partial_card["expiry_year"] = ex.expiry_year

    # ── Dispatch to current state handler ────────────────────────────────────

    def _dispatch(self, ex: TurnExtraction) -> str:
        match self.state:
            case State.COLLECTING_ACCOUNT_ID:
                return self._step_collect_id(ex)
            case State.VERIFYING:
                return self._step_verify(ex)
            case State.COLLECTING_AMOUNT:
                return self._step_collect_amount(ex)
            case State.COLLECTING_CARD:
                return self._step_collect_card(ex)
            case State.CONFIRMING:
                return self._step_confirm(ex)
            case _:
                return MSG["not_understood"]

    # ── State: COLLECTING_ACCOUNT_ID ─────────────────────────────────────────

    def _step_collect_id(self, ex: TurnExtraction) -> str:
        if not ex.account_id:
            return MSG["ask_id"]

        try:
            result = lookup_account(ex.account_id)
        except LookupUnavailable:
            return MSG["lookup_fail"]

        # lookup_account returns AccountNotFound (not raises) on 404
        if isinstance(result, AccountNotFound):
            return MSG["id_not_found"]

        self.account = result
        self.state = State.VERIFYING

        # Fast-path: user volunteered identity info before we even asked
        if self.claimed.is_complete():
            return self._attempt_verify()
        if self.claimed.full_name and not self.claimed.has_secondary_factor():
            return MSG["ask_secondary"]
        return MSG["ask_identity"]

    # ── State: VERIFYING ─────────────────────────────────────────────────────

    def _step_verify(self, ex: TurnExtraction) -> str:
        if ex.intent == "question":
            return MSG["no_data"]

        if not self.claimed.is_complete():
            if self.claimed.full_name and not self.claimed.has_secondary_factor():
                return MSG["ask_secondary"]
            return MSG["ask_identity"]

        return self._attempt_verify()

    def _attempt_verify(self) -> str:
        if verify_user(self.claimed, self.account):
            self.state = State.COLLECTING_AMOUNT
            if self.account.balance <= 0:
                return self._close(MSG["balance_zero"])
            # Pre-provided amount (out-of-order) — validate and skip ahead
            if self.amount is not None and self._amount_valid():
                return self._proceed_to_card()
            return MSG["ask_amount"].format(balance=self.account.balance)

        self.verify_attempts += 1
        self.claimed = ClaimedIdentity()  # wipe; don't hint what matched
        if self.verify_attempts >= MAX_VERIFY_ATTEMPTS:
            return self._close(MSG["verify_locked"])
        return MSG["verify_fail"]

    # ── State: COLLECTING_AMOUNT ─────────────────────────────────────────────

    def _step_collect_amount(self, ex: TurnExtraction) -> str:
        if ex.intent == "question":
            return MSG["no_data"]

        # "pay it all" / "clear the balance" → confirm intent with no amount
        if ex.intent == "confirm" and self.amount is None:
            self.amount = Decimal(str(self.account.balance))

        if self.amount is None:
            return MSG["ask_amount"].format(balance=self.account.balance)

        if not self._amount_valid():
            self.amount = None
            return MSG["amount_invalid"].format(balance=self.account.balance)

        return self._proceed_to_card()

    def _amount_valid(self) -> bool:
        if self.amount is None or self.account is None:
            return False
        try:
            a = self.amount
            balance = Decimal(str(self.account.balance))
            return (
                a > 0
                and a <= balance
                and (-a.as_tuple().exponent) <= 2
            )
        except (InvalidOperation, Exception):
            return False

    def _proceed_to_card(self) -> str:
        self.state = State.COLLECTING_CARD
        if self._card_complete():
            return self._try_finalise_card()
        return MSG["ask_card"]

    # ── State: COLLECTING_CARD ───────────────────────────────────────────────

    def _step_collect_card(self, ex: TurnExtraction) -> str:
        if ex.intent == "question":
            return MSG["no_data"]

        if not self._card_complete():
            return MSG["ask_card"]

        return self._try_finalise_card()

    def _card_complete(self) -> bool:
        required = {"cardholder_name", "card_number", "cvv", "expiry_month", "expiry_year"}
        return required.issubset(self._partial_card)

    def _try_finalise_card(self) -> str:
        try:
            self.card = Card(**self._partial_card)
            self.state = State.CONFIRMING
            return self._confirm_prompt()
        except ValidationError as exc:
            return self._card_validation_error(exc)

    def _card_validation_error(self, exc: ValidationError) -> str:
        msgs_lower = " ".join(str(e["msg"]) for e in exc.errors()).lower()
        if any(k in msgs_lower for k in ("checksum", "luhn", "digits only", "length is invalid")):
            self._partial_card.pop("card_number", None)
            return MSG["card_luhn"]
        if "cvv" in msgs_lower:
            self._partial_card.pop("cvv", None)
            last4 = str(self._partial_card.get("card_number", "????"))[-4:]
            return MSG["ask_cvv"].format(last4=last4)
        if "expired" in msgs_lower:
            self._partial_card.pop("expiry_month", None)
            self._partial_card.pop("expiry_year", None)
            return MSG["card_expired"]
        # Unknown validation error — clear everything and start over
        self._partial_card = {}
        return MSG["card_invalid"]

    # ── State: CONFIRMING ────────────────────────────────────────────────────

    def _step_confirm(self, ex: TurnExtraction) -> str:
        if ex.intent == "question":
            return MSG["no_data"]

        if ex.intent == "confirm":
            return self._do_payment()

        if ex.intent in ("deny", "cancel"):
            # Let the user correct the amount or card
            self.amount = None
            self.card = None
            self._partial_card = {}
            self.state = State.COLLECTING_AMOUNT
            return MSG["ask_amount"].format(balance=self.account.balance)

        return self._confirm_prompt()

    def _confirm_prompt(self) -> str:
        if self.card:
            last4 = self.card.card_number[-4:]
        else:
            last4 = str(self._partial_card.get("card_number", "????"))[-4:]
        return MSG["confirm_payment"].format(amount=self.amount, last4=last4)

    # ── Payment execution ────────────────────────────────────────────────────

    def _do_payment(self) -> str:
        request = PaymentRequest(
            account_id=self.account.account_id,
            amount=self.amount,
            payment_method=PaymentMethod(card=self.card),
        )
        try:
            response = process_payment(request)
        except PaymentDeclined as e:
            return self._handle_declined(e.error_code)
        except PaymentOutcomeUnknown:
            self._clear_card()
            return self._close(MSG["payment_unknown"])
        except PaymentUnavailable:
            self._clear_card()
            return MSG["payment_fail"]

        amount = self.amount
        self._clear_card()
        self.state = State.COMPLETE
        return MSG["payment_ok"].format(amount=amount, txn_id=response.transaction_id)

    def _handle_declined(self, code: str) -> str:
        if code == "invalid_cvv":
            last4 = self.card.card_number[-4:]
            self._partial_card.pop("cvv", None)
            self.card = None
            self.state = State.COLLECTING_CARD
            return MSG["ask_cvv"].format(last4=last4)

        if code in ("insufficient_balance", "invalid_amount"):
            self.amount = None
            self.card = None
            self._partial_card = {}
            self.state = State.COLLECTING_AMOUNT
            return MSG["amount_invalid"].format(balance=self.account.balance)

        if code == "expired_card":
            self._partial_card.pop("expiry_month", None)
            self._partial_card.pop("expiry_year", None)
            self.card = None
            self.state = State.COLLECTING_CARD
            return MSG["card_expired"]

        self.card = None
        self._partial_card = {}
        self.state = State.COLLECTING_CARD
        return MSG["card_invalid"]

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _close(self, message: str) -> str:
        self.state = State.CLOSED
        self._clear_card()
        return message

    def _clear_card(self) -> None:
        self.card = None
        self._partial_card = {}
