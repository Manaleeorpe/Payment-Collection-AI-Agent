# Payment Collection Agent

Conversational AI agent for end-to-end payment collection over chat.

## File structure

| File | Role |
|---|---|
| `VerifyUser.py` | `Account`, `ClaimedIdentity` models; `lookup_account`, `verify_user`; error classes |
| `ProcessPayment.py` | `Card`, `PaymentRequest`, `PaymentResponse` models; `process_payment`; error classes |
| `extraction.py` | `TurnExtraction` Pydantic model; `extract()` — wraps LLM with structured output |
| `agent.py` | `Agent` class, 7-state machine, all user-facing strings in `MSG` |
| `cli.py` | Interactive REPL |
| `evaluate.py` | Full test harness (extraction + validators + flow + interface) |
| `_smoke.py` | Flow tests only — quick sanity check |

## Setup

```bash
pip install -r requirements.txt
```

`.env` template:

```
OPENAI_API_KEY=sk-...
baseURL=https://your-api-host
```

## Run

Interactive CLI:

```bash
python cli.py
```

Full test suite (real LLM + real API):

```bash
python evaluate.py
```

Flow tests only (faster):

```bash
python _smoke.py
```

---

## Test suite

`evaluate.py` is organised in four sections:

### Section 1 — Extraction tests (19 cases)
Tests `extract()` in isolation with the real LLM. No agent state, no API calls.
Each case feeds one user phrase and asserts specific `TurnExtraction` fields.
Covers every variant from the spec table: account ID normalisation, name aliases,
three DOB formats, spaced pincode, Aadhaar + question intent, spelled-out amounts,
"pay full" → confirm intent, spaced card number, short expiry, spoken CVV.

### Section 2 — Validator unit tests (20 cases)
Pure Python — no LLM, no network. Tests `Card`, `PaymentRequest`, and `verify_user`
directly by constructing objects and asserting pass/fail.

| What is tested | Cases |
|---|---|
| Card: Luhn fail, wrong length, Amex 4-digit CVV, 2-digit CVV, expired, bad month | 7 |
| PaymentRequest: amount zero, negative, >2 decimal places | 3 |
| verify_user: exact match via DOB / aadhaar / pincode; lowercase, uppercase, double-space, first-name-only, wrong secondary | 10 |

### Section 3 — End-to-end flow tests (29 cases)
Full agent conversations — real LLM extraction + real API calls — except for
three scenarios the sandbox cannot provoke (noted below).

**Real API tests:**
- Happy path clean inputs, messy natural-language inputs
- Out-of-order: identity fields in turn 1, agent must not re-ask them
- Secondary factor variants: aadhaar, pincode, DOB all tested separately
- ACC1002 (long name, partial payment ₹300 of ₹540)
- ACC1003 (zero balance — closes gracefully after verify, no payment attempted)
- ACC1004 (real leap-year DOB 1988-02-29 accepted)
- ACC1004 three-way split: impossible date 1989-02-29 (no retry burned) vs valid-wrong 1988-02-28 (burns one retry) vs correct 1988-02-29
- account_not_found (ACC9999) then recover
- invalid_amount (0, negative) caught locally — asserts exactly 1 API call after correction
- insufficient_balance (amount > balance) caught locally — asserts 1 API call
- Luhn fail, 2-digit CVV, Amex 3-digit CVV, expired card — all caught locally — assert 0 extra API calls
- Verification lockout (3 real-name mismatches → CLOSED → 0 payment calls)
- Retry limit terminal (correct details after lockout still refused)
- Cardholder name different from account holder (API confirmed: not validated)
- Correction mid-flow (updated DOB takes effect)
- Cancel mid-flow (clean close, 0 payment calls)
- Adversarial: ask for DOB, spouse claims balance, prompt injection — all refused
- Empty / whitespace inputs do not crash

**Mocked (sandbox cannot provoke these):**
- `invalid_cvv` from process-payment — sandbox accepts any CVV
- `PaymentOutcomeUnknown` — genuine timeout
- `insufficient_balance` from API as a race condition

### Section 4 — Interface compliance (4 cases)
- Two `Agent()` instances share no state
- `CLOSED` state returns identical message on every subsequent call
- Every `next()` returns a dict with a non-empty `"message"` key
- Global data-leakage check: re-runs all flow cases and greps every agent message for each account's real DOB, Aadhaar, and pincode

---

## Real account data (discovered by probing the API)

| Account | Name | DOB | Aadhaar last 4 | Pincode | Balance |
|---|---|---|---|---|---|
| ACC1001 | Nithin Jain | 1990-05-14 | 4321 | 400001 | ₹1250.75 |
| ACC1002 | Rajarajeswari Balasubramaniam | 1985-11-23 | 9876 | 400002 | ₹540.00 |
| ACC1003 | Priya Agarwal | 1992-08-10 | 2468 | 400003 | ₹0.00 |
| ACC1004 | Rahul Mehta | 1988-02-29 | 1357 | 400004 | ₹3200.50 |

---

## Transcripts

### 1. Successful end-to-end payment

```
Agent: Welcome! Please provide your account ID to get started.
You:   ACC1001
Agent: Please provide your full name and one of: date of birth (YYYY-MM-DD),
       last 4 digits of Aadhaar, or 6-digit pincode.
You:   Nithin Jain, aadhaar 4321
Agent: How much would you like to pay? Your current balance is ₹1250.75.
You:   500
Agent: Please provide your card details: cardholder name, card number,
       CVV, and expiry date (MM/YYYY).
You:   Nithin Jain, 4532015112830366, CVV 123, expiry 12/2027
Agent: Please confirm: Pay ₹500.00 using the card ending 0366? (yes / no)
You:   yes
Agent: Payment of ₹500.00 successful. Transaction ID: txn_178...abc. Thank you!
```

### 2. Verification failure to lockout

```
Agent: Welcome! Please provide your account ID to get started.
You:   ACC1001
Agent: Please provide your full name and one of: date of birth (YYYY-MM-DD),
       last 4 digits of Aadhaar, or 6-digit pincode.
You:   Amit Kumar, DOB 1985-03-22
Agent: I couldn't verify your identity. Please try again with your full name
       and a secondary factor.
You:   Sneha Patel, pincode 560034
Agent: I couldn't verify your identity. Please try again with your full name
       and a secondary factor.
You:   Vikas Gupta, aadhaar 9999
Agent: Too many failed verification attempts. This session is now closed.
You:   Nithin Jain, aadhaar 4321
Agent: This session is closed. Please start a new conversation to try again.
```

### 3. Payment declined — invalid CVV from API, then corrected

```
Agent: Welcome! Please provide your account ID to get started.
You:   ACC1001
Agent: Please provide your full name and one of: ...
You:   Nithin Jain, aadhaar 4321
Agent: How much would you like to pay? Your current balance is ₹1250.75.
You:   200
Agent: Please provide your card details: ...
You:   Nithin Jain, 4532015112830366, CVV 999, expiry 12/2027
Agent: Please confirm: Pay ₹200.00 using the card ending 0366? (yes / no)
You:   yes
Agent: The CVV doesn't match. Please re-enter the CVV for the card ending 0366.
You:   123
Agent: Please confirm: Pay ₹200.00 using the card ending 0366? (yes / no)
You:   yes
Agent: Payment of ₹200.00 successful. Transaction ID: txn_178...xyz. Thank you!
```

Note: the sandbox accepts any CVV; this scenario uses a mocked payment response
to prove the agent handles the `invalid_cvv` error code correctly.

### 4. Edge case — identity provided out of order in turn 1

```
Agent: Welcome! Please provide your account ID to get started.
You:   ACC1001 Nithin Jain DOB 14-05-1990
Agent: How much would you like to pay? Your current balance is ₹1250.75.
You:   300
Agent: Please provide your card details: cardholder name, card number,
       CVV, and expiry date (MM/YYYY).
You:   Nithin Jain, 4532015112830366, CVV 123, expiry 12/2027
Agent: Please confirm: Pay ₹300.00 using the card ending 0366? (yes / no)
You:   yes
Agent: Payment of ₹300.00 successful. Transaction ID: txn_178...def. Thank you!
```

Identity fields (name, DOB) extracted and merged in turn 1 — agent skips
directly to asking for the amount without re-asking for name or secondary factor.
