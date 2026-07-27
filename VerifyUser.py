import os

import os
from datetime import date

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict
from tenacity import retry, stop_after_attempt, wait_random_exponential

load_dotenv()

baseURL = os.getenv("baseURL")

class LookupError_(Exception):
    """Base for account lookup failures."""

class AccountNotFound(LookupError_):
    def __init__(self, account_id: str):
        self.account_id = account_id
        super().__init__(f"No account found for {account_id}")

class LookupUnavailable(LookupError_):
    """Network failure, 5xx, malformed response — not the user's fault."""


class Account(BaseModel):
    model_config= ConfigDict(extra="ignore")
    account_id: str 
    full_name: str 
    dob:date
    aadhaar_last4:str
    pincode:str
    balance: float
    

class ClaimedIdentity(BaseModel):
    """What the user has told us so far. Fills up across turns."""
    full_name: str | None = None
    dob: date | None = None
    aadhaar_last4: str | None = None
    pincode: str | None = None

    def has_secondary_factor(self) -> bool:
        return any([self.dob, self.aadhaar_last4, self.pincode])

    def is_complete(self) -> bool:
        return bool(self.full_name) and self.has_secondary_factor()


@retry(stop=stop_after_attempt(3), wait=wait_random_exponential(multiplier=1, min=1, max=10))
def lookup_account(account_id:str) -> Account:
    """Fetch account details. Raises AccountNotFound or LookupUnavailable."""
    url = baseURL + "/api/lookup-account"
    payload = {
        "account_id": account_id
    }
    response = requests.post(url=url, json=payload, timeout=10)
    if response.status_code==404:
        return AccountNotFound(account_id=account_id)
    if not response.ok:
        raise LookupUnavailable(f"lookup returned {response.status_code}")
    try:
        return Account.model_validate(response.json())
    except ValueError as e:
        raise LookupUnavailable(f"unexpected response shape: {e}") from e

def verify_user(claimed:ClaimedIdentity, account:Account) -> bool:

    if claimed.full_name != account.full_name:      # strict
        return False

    return any([
        claimed.dob is not None           and claimed.dob == account.dob,
        claimed.aadhaar_last4 is not None and claimed.aadhaar_last4 == account.aadhaar_last4,
        claimed.pincode is not None       and claimed.pincode == account.pincode,
    ])


    