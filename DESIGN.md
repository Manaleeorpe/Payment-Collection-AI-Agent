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

---

## Key decisions

### 1. The LLM extracts and Python decides

The LLM's only jobs are parsing messy text into typed fields and classifying
intent. Every control-flow decision whether identity checks passed, whether to
call an API, which state to move to is deterministic Python. The API functions
are never exposed as tools the model could invoke.

**Why:** LLM-driven tool calling is non-deterministic and hard to audit for
security properties. A pure Python gate is unit-testable, auditable, and does
not depend on prompt wording.

### 2. Verification is mandatory for processing payment requests

```python
claimed.full_name == account.full_name   # exact, case-sensitive
AND any(dob_match | aadhaar_match | pincode_match)
```

This runs in `verify_user()` (VerifyUser.py), which is pure so it can
be unit-tested by constructing objects directly. `process_payment` is unreachable
from any state that has not set `self.state = State.COLLECTING_AMOUNT`, which
only happens after a successful call to `verify_user`.

**Why:** Identity verification is a security control. Placing it in an LLM
prompt allows prompt injection and accidental near-miss acceptance.
A two-line Python comparison is the only correct implementation.

### 3. The extraction prompt never receives account data

`extract()` receives only user text and conversation history. It never sees
`full_name`, `dob`, `aadhaar_last4`, or `pincode` from the API response.
Comparison happens in Python against data the model never had.

**Why:** If the model saw the account record, it could leak it into replies,
and the agent can help the user's to claim the match, creating a false accept.

### 4. No account data in user-facing strings

All reply strings are templates in `MSG` (agent.py). Verification and balance
messages use only the values the user already provided (amount, last 4 of card).
The account's name, DOB, Aadhaar, and pincode never appear in any branch of the
reply logic.

**Why:** Template strings are auditable in one place. LLM-generated replies
could accidentally include whatever was in the prompt context.

### 5. `process_payment` is never auto-retried

`PaymentOutcomeUnknown` (raised on network timeout) closes the session
immediately. The user is told to check their statement.

**Why:** A timeout means the charge may
have gone through. Retrying risks a double charge. This is non-negotiable in any
payment system.

### 6. Terminal states are terminal

Once `CLOSED` or `COMPLETE`, every subsequent `next()` call returns a fixed
message and mutates nothing. A user who provides correct credentials after
exhausting the retry limit is still refused.

**Why:** The retry limit is a security control

### 7. Card data hygiene

Raw PANs are regex-extracted from user text _before_ the LLM call, so they
never reach the upstream model. Card fields are cleared from state once payment
terminates (success or unknown outcome). `Card.__repr__` already redacts the
PAN and CVV so they don't appear in logs or tracebacks.

---

## Tradeoffs accepted

| Decision                                    | Tradeoff                                                                 |
| ------------------------------------------- | ------------------------------------------------------------------------ |
| `temperature=0`                             | Deterministic extraction but may miss highly creative phrasings          |
| Exact name match                            | No tolerance for typos; user must spell their name exactly as registered |
| Full card re-entry on most decline codes    | Simpler and safer than trying to guess which field is wrong              |
| History capped at 10 turns for LLM          | Prevents token blowout; old turns rarely contain new extractable data    |
| Retry `lookup_account` up to 3× with jitter | Transient network failures should not fail the user; 404 is not retried  |

---

## Determinism tension

An LLM sits in the extraction loop, introducing a source of non-determinism.
Mitigations:

- `temperature=0` minimises sampling variance
- `with_structured_output` forces a typed response; the model cannot choose
  to do something else
- All decisions and state transitions are in Python — the LLM cannot alter them
- The evaluator mocks the LLM indirectly (mocks the API calls) and runs the
  real extraction, so evaluation covers actual extraction quality

---

## Assumptions and threat model

**No authentication layer.** The agent performs only in-session identity
verification (name + secondary factor). There is no session token, no login,
and no persistent credential. An attacker who can guess an account ID and pass
the identity check gains access. The retry limit (3 attempts) is the only
brute-force protection. Production would add rate limiting and MFA.

**Sandbox does not persist balance changes.** A second payment attempt in the
same session would see the same balance as before. The agent does not re-fetch
the balance between payment attempts; it uses the balance from the initial
`lookup_account` response.

**Single-turn card entry.** The user is expected to provide all card fields in
one or two turns. The partial-card accumulation handles multi-turn entry, but
there is no timeout or expiry on partial state within a session.

---

## What I would improve with more time

1. **Re-fetch balance before payment** — avoids stale-balance edge cases in
   long sessions.
2. **Fuzzy card field detection** — ask specifically for the missing field
   (e.g. "I have your card number and name; what is the CVV?") instead of
   asking for all card details again when only one field is missing.
3. **Extraction unit tests** — a fixture set of (raw_text, expected_extraction)
   pairs that run against the real LLM, pinned to a model version.
4. **Rate limiting** — wrap the verification gate with per-account attempt
   counting persisted across sessions.
5. **Structured logging** — emit structured events (JSON) for each state
   transition, excluding sensitive fields, for observability.
