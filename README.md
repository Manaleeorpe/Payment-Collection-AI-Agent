# Payment Collection Agent

Conversational AI agent for end-to-end payment collection over chat.

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

Automated evaluation (mocked API, live LLM):

```bash
python evaluate.py
```

---

## Transcripts

### 1. Successful end-to-end payment

```
Agent: Welcome! Please provide your account ID to get started.
You:   ACC1001
Agent: Please provide your full name and one of: date of birth (YYYY-MM-DD),
       last 4 digits of Aadhaar, or 6-digit pincode.
You:   Nithin Jain, DOB 14th May 1990
Agent: How much would you like to pay? Your current balance is ₹5000.00.
You:   500
Agent: Please provide your card details: cardholder name, card number,
       CVV, and expiry date (MM/YYYY).
You:   Nithin Jain, 4532015112830366, CVV 123, expiry 12/2027
Agent: Please confirm: Pay ₹500.00 using the card ending 0366? (yes / no)
You:   yes
Agent: Payment of ₹500.00 successful. Transaction ID: TXN-ABC123. Thank you!
```

### 2. Verification failure to lockout

```
Agent: Welcome! Please provide your account ID to get started.
You:   ACC1001
Agent: Please provide your full name and one of: date of birth (YYYY-MM-DD),
       last 4 digits of Aadhaar, or 6-digit pincode.
You:   John Smith, DOB 1980-01-01
Agent: I couldn't verify your identity. Please try again with your full name
       and a secondary factor.
You:   Jane Doe, pincode 123456
Agent: I couldn't verify your identity. Please try again with your full name
       and a secondary factor.
You:   Random Name, aadhaar 9999
Agent: Too many failed verification attempts. This session is now closed.
You:   Nithin Jain, DOB 1990-05-14
Agent: This session is closed. Please start a new conversation to try again.
```

### 3. Payment declined — invalid CVV, then corrected

```
Agent: Welcome! Please provide your account ID to get started.
You:   ACC1001
Agent: Please provide your full name and one of: ...
You:   Nithin Jain, aadhaar 1234
Agent: How much would you like to pay? Your current balance is ₹5000.00.
You:   200
Agent: Please provide your card details: ...
You:   Nithin Jain, 4532015112830366, CVV 999, expiry 12/2027
Agent: Please confirm: Pay ₹200.00 using the card ending 0366? (yes / no)
You:   yes
Agent: The CVV doesn't match. Please re-enter the CVV for the card ending 0366.
You:   123
Agent: Please confirm: Pay ₹200.00 using the card ending 0366? (yes / no)
You:   yes
Agent: Payment of ₹200.00 successful. Transaction ID: TXN-XYZ789. Thank you!
```

### 4. Edge case — out-of-order information in turn 1

```
Agent: Welcome! Please provide your account ID to get started.
You:   My account is ACC1001, name is Nithin Jain, DOB 14-05-1990,
       want to pay 300 rupees
Agent: Please provide your card details: cardholder name, card number,
       CVV, and expiry date (MM/YYYY).
You:   Nithin Jain, 4532015112830366, CVV 123, expiry 12/2027
Agent: Please confirm: Pay ₹300.00 using the card ending 0366? (yes / no)
You:   yes
Agent: Payment of ₹300.00 successful. Transaction ID: TXN-DEF456. Thank you!
```

> All identity and payment details in turn 1 are extracted, merged, and used
> immediately — no re-asking for already-provided information.
