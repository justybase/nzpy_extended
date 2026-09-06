# Production adaptation matrix

> Document type: production adaptation matrix
> Status: maintained template
> Applies to: transition from the demo to a bank reporting platform
> Production adaptation: every row requires confirmation
> Owner: architecture, security, data and platform owners

This example demonstrates reporting behaviour. It does not decide the bank's
identity provider, deployment topology, data classification, operational
ownership or regulatory controls. Those decisions must be recorded here and in
an ADR before production approval.

Status values used in this document:

- `DEMO ONLY` — never promote unchanged;
- `DECISION REQUIRED` — the target environment must choose an option;
- `CONTROL REQUIRED` — evidence is required before go-live;
- `EXAMPLE COMPATIBLE` — the pattern can be retained after review.

## Decision register

| Area | Example now | Production decision required | Minimum evidence/control | Owner |
|---|---|---|---|---|
| Identity | In-process fake LDAP and demo users | OIDC, SAML, corporate LDAP or another approved IdP | SSO integration test, claim-to-role mapping, logout and revocation behaviour | Security / IAM |
| Tokens | Local HS256 JWT with configurable secret | Central key management, JWKS, key rotation and token lifetime | Key rotation test, expiry test, incident revocation procedure | Security |
| Credentials | Public demo credentials in documentation | No credentials in source or shared documentation | Secret scan, secret-manager reference, rotation record | Security / Platform |
| Authorization | Role sets and scope checks in application code | Source of roles, entitlements and approval workflow | Positive/negative scope tests and access review evidence | Security / Business owner |
| Transport | Local HTTP-friendly demo defaults | TLS termination, secure cookies, proxy headers and CSRF policy | Deployment security review and automated header checks | Platform |
| Data classification | Demo anonymised identifiers | Classification of customer, employee, financial and operational data | Data inventory, masking rules and retention approval | Data governance |
| Database account | Netezza read access supplied by environment variables | Vault-managed, least-privileged, environment-specific service account | Permission review and failed-write test | Data platform |
| ETL publication | `MIS_CONTROL_DATASET_LOAD` with publication-last convention | Keep the table or replace it with an approved orchestration/event contract | Reconciliation evidence, run ID lineage and publication ordering test | Data engineering |
| ETL quality | ETL owns checks; application trusts `PUBLISHED` | Decide whether per-table manifest or stronger application validation is required | Row count, watermark, checksum and completeness evidence | Data engineering |
| Late data | Demo has deterministic dates | Define backfill, restatement and re-publication policy | Reconciliation example and historical report acceptance test | Data engineering / Business owner |
| RAM cache | Full eager table set in process memory | Determine allowed memory, masking and table-specific storage strategy | Capacity benchmark and memory limit alert | Platform / Architecture |
| SQLite snapshot | Local unencrypted full snapshot, one host/process | Decide encrypted local cache, shared cache, Redis, warehouse mart or no persistence | Encryption, permissions, backup/restore and corruption test | Platform / Security |
| SQLite I/O | Synchronous local operations behind async methods | Move blocking work to a bounded worker or choose a different store | Event-loop latency test under refresh load | Architecture |
| Topology | One process/host assumption | Single instance, multi-worker or multi-host deployment | Locking, generation consistency and rolling restart test | Platform |
| Availability | Last complete generation served when source is unavailable | Define fail-open/fail-closed policy for stale reporting | SLO, alert threshold, incident runbook and business approval | Business owner / Operations |
| Recovery | SQLite can reduce restart reload time | Define RTO, RPO, snapshot backup and restore authority | Timed restore drill and documented evidence | Operations |
| Audit | Application logs and report freshness metadata | Central immutable audit for login, report access, export and cache actions | Audit event schema, retention and retrieval test | Security / Compliance |
| Observability | Logs and `/api/status` | Central metrics, traces, logs, alerts and dashboards | SLO dashboard and alert-response runbook | Operations |
| API lifecycle | Unversioned `/api` paths | Versioning, deprecation, compatibility and consumer notification | OpenAPI diff policy and compatibility test | API owner |
| Error handling | FastAPI `detail` responses vary by route | Standard error envelope, correlation ID and retry classification | Error contract test and support examples | API owner |
| Exports | XLSX/XLSB files generated on demand | Data-loss prevention, row caps, watermarking, retention and malware scanning | Export security test and file lifecycle evidence | Security / Operations |
| Large analytics | Some joins and aggregation happen in Python | Pushdown, aggregate marts, pagination and workload isolation | Production-size benchmark and query plan review | Data platform |
| Time semantics | Demo uses ISO dates and fixed sample calendar | Define timezone, business calendar, holidays and cut-off policy | Boundary-date tests and business sign-off | Business owner / Data engineering |
| Schema changes | `seed.py` is the demo schema source | Database migration and backward-compatibility process | Migration rehearsal and rollback evidence | Data platform |
| Testing | Unit tests and optional browser checks | Masked data, integration, contract, load, security and DR tests | Release gate with retained results | QA / Platform |
| Ownership | Example has no operational RACI | Support tier, on-call, data owner and report owner | RACI and escalation contacts | Architecture |

## Non-negotiable demo boundaries

The following must not be treated as production-ready defaults:

- `FakeLDAPService`;
- documented demo passwords;
- the default JWT secret;
- `reload=True` in the local launcher;
- `seed.py`, because it drops and recreates demo tables;
- an unencrypted local SQLite file containing the full unmasked cache;
- one-process cache locking as a multi-worker coordination mechanism;
- client-side hiding as an authorization control;
- Python in-memory aggregation without a production-size capacity test.

## Go-live evidence checklist

- [ ] approved identity and role mapping;
- [ ] no demo credentials or default secrets;
- [ ] data classification and masking approved;
- [ ] database permissions reviewed;
- [ ] ETL publication and reconciliation evidence available;
- [ ] cache topology and encryption approved;
- [ ] stale-data business policy approved;
- [ ] RTO/RPO and restore drill completed;
- [ ] audit events integrated and queryable;
- [ ] monitoring, alerting and on-call ownership assigned;
- [ ] production-size performance results accepted;
- [ ] API compatibility policy approved;
- [ ] security, privacy and architecture reviews completed.
