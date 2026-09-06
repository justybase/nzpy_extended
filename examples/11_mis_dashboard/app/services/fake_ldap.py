"""A deterministic, in-process LDAP-like directory for the demo.

This intentionally is not a network LDAP implementation.  It provides the
same small boundary the application would use with LDAP in production while
keeping the example self-contained and easy to run.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass


PBKDF2_ITERATIONS = 310_000


@dataclass(frozen=True)
class LDAPAccount:
    username: str
    user_code: str
    password_salt: str
    password_hash: str
    can_switch_persona: bool = False


# Passwords are documented in docs/authentication.md for this deliberately
# public demo.  Only deterministic PBKDF2 hashes are kept in application code.
_ACCOUNTS = (
    LDAPAccount(
        "mis.sql", "MIS_SQL_DEV01", "6d69732d64656d6f2d73616c742d3031",
        "88c2bb53e55e9e05c92b97c7214ca439d9838983cc685ec7be4c3ae51ea4d5cf",
    ),
    LDAPAccount(
        "network.head", "NET_HEAD01", "6d69732d64656d6f2d73616c742d3032",
        "3dd8cc8cc3a1e68b1d643aa7942362debc1ded175d664e5d7c3720ad85dba82c",
    ),
    LDAPAccount(
        "regional.director", "REG_DIR_RNOR", "6d69732d64656d6f2d73616c742d3033",
        "5c7a43ede3b124a03f99dd69fc9262ffb6393960fbd454540c4bfd92946f943d",
    ),
    LDAPAccount(
        "branch.director", "BR_DIR_BEL01", "6d69732d64656d6f2d73616c742d3034",
        "a3039140bfff3a701f59ea2c48c11192f8601f611fec62aaa164fa8dac1efafe",
    ),
    LDAPAccount(
        "customer.advisor", "ADV_DEMO_P0001", "6d69732d64656d6f2d73616c742d3035",
        "df1554a95a2f5537d4e8ac7aac4f0495f8ad909a835295e75ce000d527358952",
    ),
    LDAPAccount(
        "hq.full", "HQ_FULL01", "6d69732d64656d6f2d73616c742d3036",
        "46283f2a0fb50fa06e8f97fbb2ef27c0740c066ebe1823609b7a4d5cbfe9bcdb",
    ),
    LDAPAccount(
        "app.tester", "APP_TESTER01", "6d69732d64656d6f2d73616c742d3037",
        "e20439408d43c5d7d5d89397707f0a2bfa446624af0353f77934da3d00e8323e",
        can_switch_persona=True,
    ),
)


class FakeLDAPService:
    """Authenticate against the fixed local demo directory."""

    def __init__(self, accounts: tuple[LDAPAccount, ...] = _ACCOUNTS) -> None:
        self._accounts = {account.username: account for account in accounts}

    @staticmethod
    def _normalise_username(username: str) -> str:
        return username.strip().lower()

    @staticmethod
    def _password_digest(password: str, salt_hex: str) -> str:
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex),
            PBKDF2_ITERATIONS,
        )
        return digest.hex()

    def account(self, username: str) -> LDAPAccount | None:
        return self._accounts.get(self._normalise_username(username))

    def authenticate(self, username: str, password: str) -> LDAPAccount | None:
        account = self.account(username)
        if account is None:
            return None
        candidate = self._password_digest(password, account.password_salt)
        if not hmac.compare_digest(candidate, account.password_hash):
            return None
        return account
