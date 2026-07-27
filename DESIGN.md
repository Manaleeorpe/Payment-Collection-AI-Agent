# Design Document — Payment Collection Agent

## Architecture overview

```
User text
    │
    ▼
extraction.py  ←  LLM (gpt-4o-mini, temperature=0, structured output)
    │ TurnExtraction (all fields optional)
    ▼
agent.py  ─── pure Python state machine
    ├── _merge()      accumulate fields across turns
    ├── _dispatch()   route to current state handler
    └── state handlers call:
          lookup_account()   [VerifyUser.py]
          verify_user()      [VerifyUser.py] — pure, no I/O
          process_payment()  [ProcessPayment.py]
```

The agent exposes a single method: `next(user_input: str) -> {"message": str}`.
All state is internal. No setup between turns.

### Source files

| File | Contains |
|---|---|
| `VerifyUser.py` | `Account`, `ClaimedIdentity`; `LookupError_`, `AccountNotFound`, `LookupUnavailable`; `lookup_account` (retried), `verify_user` (pure) |
| `ProcessPayment.py` | `Card`, `PaymentMethod`, `PaymentRequest`, `PaymentResponse`; `PaymentError`, `PaymentDeclined`, `PaymentUnavailable`, `PaymentOutcomeUnknown`; `process_payment` |
| `extraction.py` | `TurnExtraction` (all fields optional); `extract()` — calls LLM, redacts card PANs before sending |
| `agent.py` | `State` enum, `Agent` class, `MSG` string table |
| `cli.py` | Interactive REPL |
| `evaluate.py` | Full test harness |
| `_smoke.py` | Flow tests only — fast sanity check |

---

## Key decisions

### 1. The LLM extracts; Python decides

The LLM's only jobs are parsing messy text into typed fields and classifying
intent. Every control-flow decision — whether identity checks passed, whether to
call an API, which state to move to — is deterministic Python. The API functions
are never exposed as tools the model could invoke.

**Why:** LLM-driven tool calling is non-deterministic and hard to audit for
security properties. A pure Python gate is unit-testable, auditable, and does
not depend on prompt wording.

### 2. Verification is a hard gate in code, not a prompt instruction

```python
claimed.full_name == account.full_name   # exact, case-sensitive
AND any(dob_match | aadhaar_match | pincode_match)
```

This runs in `verify_user()` (VerifyUser.py), which is pure (no I/O) so it can
be unit-tested by constructing objects directly with no mocking. `process_payment`
is unreachable from any state that has not passed this gate — the state machine
only sets `State.COLLECTING_AMOUNT` after `verify_user` returns `True`.

**Why:** Placing verification in an LLM prompt allows prompt injection and
accidental near-miss acceptance. A two-line Python comparison is the only
correct implementation.

### 3. The extraction prompt never receives account data

`extract()` receives only user text and conversation history. It never sees
`full_name`, `dob`, `aadhaar_last4`, or `pincode` from the lookup response.
Comparison happens in Python against data the model never had.

**Why:** If the model saw the account record it could (a) leak it into replies
and (b) "helpfully" normalise the user's claim to match, creating a false accept.

### 4. No account data in user-facing strings

All reply strings are templates in `MSG` (agent.py). Verification and balance
messages use only values the user already provided (amount, last 4 of card).
The account's name, DOB, Aadhaar, and pincode never appear in any branch of the
reply logic.

**Why:** Template strings are auditable in one place. LLM-generated replies
could accidentally include whatever was in the prompt context.

### 5. `process_payment` is never auto-retried

`PaymentOutcomeUnknown` (raised on network timeout) closes the session immediately.
The user is told to check their statement before retrying.

**Why:** The payment endpoint is not idempotent. A timeout means the charge may
have gone through. Retrying risks a double charge.

### 6. Terminal states are terminal

Once `CLOSED` or `COMPLETE`, every subsequent `next()` call returns a fixed
closing message and mutates nothing. A user who supplies correct credentials
after exhausting the retry limit is still refused.

**Why:** State that can be exited after reaching a terminal condition is not
truly terminal. The retry limit is a security control, not a UX hint.

### 7. Card data hygiene

Raw PANs are regex-extracted from user text before the LLM call, so they never
reach the upstream model. Card fields are cleared from state once payment
terminates (success or unknown outcome). `Card.__repr__` redacts the PAN and CVV
so they do not appear in logs or tracebacks.

---

## Test suite

### Section 1 — Extraction tests (19 cases)

Tests `extract()` in isolation with the real LLM. No agent state, no API calls.
Each case asserts specific `TurnExtraction` fields from a single user phrase.
Covers every variant from the spec: three account-ID normalisations, two tricky
name cases (alias prefix, long multi-part name), three DOB formats, spaced
pincode digits, Aadhaar + question-intent combined, spelled-out amounts, "pay
full" mapping to confirm-intent with no amount, spaced card number, short expiry
year, spoken CVV digits.

### Section 2 — Validator unit tests (20 cases)

Pure Python — no LLM, no network. Objects are constructed directly and
pass/fail is asserted without any mocking.

- Card validators: Luhn fail, wrong length, Amex 4-digit CVV rule, non-Amex
  2-digit CVV, expired card, bad month (13)
- PaymentRequest: amount zero, negative, more than 2 decimal places
- verify_user: exact match via DOB / Aadhaar / pincode; case sensitivity
  (lowercase, uppercase); double-space in name; first-name-only; wrong secondary;
  long multi-part name; real leap-year DOB

### Section 3 — End-to-end flow tests (29 cases)

Full agent conversations with real LLM extraction. API strategy:

**Real API** — lookup and payment hit the live sandbox for most tests. The
sandbox was probed before writing fixtures to confirm actual account data, which
error codes it returns naturally, and what card inputs it accepts. This proves
the request payloads are correct end-to-end.

**Mocked** — only three scenarios the sandbox cannot provoke:
- `invalid_cvv` from process-payment (sandbox accepts any CVV)
- `PaymentOutcomeUnknown` (genuine network timeout)
- `insufficient_balance` as a race condition (balance changed server-side between
  lookup and payment)

For both real and mocked cases, `process_payment` is wrapped with
`MagicMock(wraps=real_fn)` so call counts are trackable without losing real
API behaviour.

Key assertions beyond final state:
- `pm.call_count == 0` after lockout, on cancel, after locally-caught bad card
- `pm.call_count == 1` after locally-caught bad amount (exactly one corrected call)
- `agent.verify_attempts` checked for the impossible-date vs valid-wrong-date split
- Sensitive data (DOB, Aadhaar, pincode) grepped from all agent messages per case

### Section 4 — Interface compliance (4 cases)

- Two `Agent()` instances share no state
- `CLOSED` returns the same message on every subsequent call (terminal is terminal)
- Every `next()` returns a dict with a non-empty `"message"` key
- Global leakage check: re-runs all 29 flow cases and greps every agent message
  for each account's real DOB, Aadhaar last 4, and pincode

---

## Tradeoffs accepted

| Decision | Tradeoff |
|---|---|
| `temperature=0` | Deterministic extraction but may miss highly creative phrasings |
| Exact name match | No tolerance for typos; user must spell their name exactly as registered |
| Full card re-entry on most decline codes | Simpler and safer than guessing which field is wrong |
| History capped at 10 turns for LLM | Prevents token blowout; old turns rarely contain new extractable data |
| Retry `lookup_account` up to 3× with jitter | Transient network failures should not fail the user; 404 is not retried |
| Extraction test names use realistic-sounding Indian names | LLM does not extract obviously fake strings like "Wrong Name" as `full_name` |

---

## Determinism tension

An LLM sits in the extraction loop, introducing a source of non-determinism.
Mitigations:

- `temperature=0` minimises sampling variance
- `with_structured_output` forces a typed response; the model cannot choose
  to do something else
- All decisions and state transitions are in Python — the LLM cannot alter them
- Extraction tests run against the real LLM at `temperature=0` and are stable
  across repeated runs

Residual risk: the same phrase may occasionally extract differently across model
versions. Production would pin the model version and run extraction regression
tests.

---

## Assumptions and threat model

**No authentication layer.** The agent performs only in-session identity
verification (name + secondary factor). There is no session token, no login, and
no persistent credential. An attacker who can guess an account ID and pass the
identity check gains access. The retry limit (3 attempts) is the only
brute-force protection. Production would add rate limiting and MFA.

**Sandbox does not persist balance changes.** A second payment attempt in the
same session would see the same balance as before. The agent does not re-fetch
the balance between payment attempts; it uses the balance from the initial
`lookup_account` response.

**Single-turn card entry.** The user is expected to provide all card fields in
one or two turns. The partial-card accumulation handles multi-turn entry, but
there is no timeout or expiry on partial state within a session.

**Cardholder name is not validated against the account holder.** The API does
not check this, confirmed by probing the sandbox. Tests cover this explicitly.

---

## What I would improve with more time

1. **Re-fetch balance before payment** — avoids stale-balance edge cases in
   long sessions.
2. **Fuzzy card field detection** — ask specifically for the missing field
   (e.g. "I have your card number and name; what is the CVV?") instead of
   re-asking for all card details when only one field is missing.
3. **Amount merge regardless of state** — currently amount is only stored when
   the state machine is in `COLLECTING_AMOUNT` or `CONFIRMING`. An amount
   volunteered in turn 1 alongside the account ID is discarded. Storing it
   unconditionally (then using it when the state machine reaches the right stage)
   would fully satisfy the out-of-order requirement.
4. **Rate limiting** — wrap the verification gate with per-account attempt
   counting persisted across sessions.
5. **Structured logging** — emit structured JSON events for each state transition,
   excluding sensitive fields, for observability.
