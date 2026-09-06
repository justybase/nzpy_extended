# Authentication and access model

> Document type: authentication and authorization contract
> Status: demo contract
> Production adaptation: corporate identity, secrets, MFA and audit required
> Owner: security / IAM

This example uses a self-contained authentication flow so it can be demoed
without a corporate identity provider. It is deliberately not a production
LDAP implementation.

## Login flow

1. The browser sends `POST /api/auth/login` with a JSON username and password.
2. `FakeLDAPService` validates the credentials against its in-process demo
   directory. Passwords are stored as PBKDF2-HMAC-SHA256 hashes.
3. `AuthService` resolves the linked `MIS_DIM_USER` row and signs a short-lived
   HS256 JWT.
4. The JWT is set as the `HttpOnly`, `SameSite=Lax` cookie
   `mis_access_token`. It is never returned in the response body.
5. Protected API calls resolve the effective persona from the signed
   `effective_user_code` claim and the current `MIS_DIM_USER` data. Roles are
   never trusted directly from browser input.

The root page and static assets are public to allow the login screen to load.
All data APIs require the cookie. Missing, expired, malformed or tampered
tokens return HTTP `401`.

## Demo accounts

These credentials are intentionally public and must only be used with this
demo dataset:

| Username | Password | Linked user code | Role |
|---|---|---|---|
| `mis.sql` | `Demo-MIS-SQL-2026!` | `MIS_SQL_DEV01` | MIS SQL developer |
| `network.head` | `Demo-Network-2026!` | `NET_HEAD01` | Head of network |
| `regional.director` | `Demo-Region-2026!` | `REG_DIR_RNOR` | Regional director (North Region) |
| `branch.director` | `Demo-Branch-2026!` | `BR_DIR_BEL01` | Branch director (Belfast Branch) |
| `customer.advisor` | `Demo-Advisor-2026!` | `ADV_DEMO_P0001` | Customer advisor (P0001) |
| `hq.full` | `Demo-HQ-2026!` | `HQ_FULL01` | HQ full access |
| `app.tester` | `Demo-Tester-2026!` | `APP_TESTER01` | Application developer / tester |

Run `python seed.py` after changing the seed code. The authentication service
does not store passwords in Netezza; `MIS_DIM_USER` stores only the linked
persona, display name, role and optional organizational scope.

## Role matrix

| Role | Reporting scope | Additional permissions |
|---|---|---|
| MIS SQL developer | Entire network | Global quality and cache refresh; this example has no arbitrary SQL endpoint |
| Network head | Entire network | Read-only business reporting and exports; global quality view |
| Regional director | One region and its branches/advisors | Read-only reporting and exports |
| Branch director | One branch and its advisors | Read-only reporting and exports |
| Customer advisor | Own advisor results and own branch aggregates | Read-only reporting and exports |
| HQ full access | Entire network | Global quality and cache refresh |
| Application developer / tester | The currently selected persona | May switch to any `MIS_DIM_USER` persona at any time |

The existing `ANALYST`, `AREA_MANAGER`, `BRANCH_MANAGER` and `ADVISOR` values
remain supported as legacy aliases with the same full, region, branch and
advisor scopes respectively.

The tester switcher is available only when the signed source account is
`app.tester`. Switching issues a new JWT, keeps `app.tester` as the source
identity for auditability, and changes the effective data scope. The selector
continues to be available while the tester is impersonating another persona.

Global quality and cache refresh are evaluated against the effective role.
Switch back to `APP_TESTER01`, `MIS_SQL_DEV01` or `HQ_FULL01` to use those
technical capabilities.

## API contract

```http
POST /api/auth/login
Content-Type: application/json

{"username":"app.tester","password":"Demo-Tester-2026!"}
```

The successful response contains the authenticated/effective `user` object;
the response also sets the cookie. `POST /api/auth/logout` clears it and is
safe to call repeatedly. `GET /api/auth/me` and `GET /api/session/me` return
the current effective user.

`GET /api/session/users` and `POST /api/session/user` are tester-only. The
latter accepts `{ "code": "BR_DIR_BEL01" }`, validates that the code exists in
`MIS_DIM_USER`, signs a replacement JWT and sets it on the response.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `MIS_JWT_SECRET` | demo-only fixed value | HS256 signing secret; set a strong secret outside a demo |
| `MIS_JWT_TTL_MINUTES` | `60` | Token lifetime |
| `MIS_JWT_ISSUER` | `mis-dashboard` | JWT issuer claim |
| `MIS_AUTH_COOKIE_NAME` | `mis_access_token` | Cookie name |
| `MIS_AUTH_COOKIE_SECURE` | `false` | Set `true` when serving over HTTPS |

Changing `MIS_JWT_SECRET` invalidates all existing tokens. This example has no
server-side token revocation list; logout removes the browser cookie and a
copied token remains valid only until its normal expiry.
