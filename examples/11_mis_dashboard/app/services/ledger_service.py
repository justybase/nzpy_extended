"""
LedgerService — server-side pagination, filtering and sorting over the full
sales detail (the "very large analytics" view).

The joined, filtered and sorted rows are computed purely from the cached
MIS_* tables (never from Netezza per request). The joined row set is cached
keyed on the repository snapshot so repeated paging / filtering does not
re-join 200k+ rows on every request.

    GET /api/ledger?from=&to=&q=&group=&channel=&status=&sort=&dir=&page=&page_size=
"""

from __future__ import annotations

from typing import Any

from app.core.roles import SessionUser
from app.repositories import AsOfMISRepository, ScopedMISRepository
from app.repositories.base import MISRepository

# (key, label, format) — order matters, it is the row layout
COLUMNS: list[tuple[str, str, str]] = [
    ("sale_id", "Sale ID", "int"),
    ("sale_date", "Date", "str"),
    ("branch_code", "Branch", "str"),
    ("branch_name", "Branch name", "str"),
    ("region_code", "Region", "str"),
    ("advisor_code", "Advisor", "str"),
    ("advisor_name", "Advisor name", "str"),
    ("product_group", "Product group", "str"),
    ("product_name", "Product", "str"),
    ("channel_name", "Channel", "str"),
    ("customer_id", "Customer ID", "int"),
    ("amount", "Amount", "eur"),
    ("commission", "Commission", "eur"),
    ("sale_status", "Status", "str"),
]

COLUMN_KEYS = [c[0] for c in COLUMNS]
SORTABLE = {c[0] for c in COLUMNS if c[0] != "sale_id"}
EXPORT_CAP = 100_000


class LedgerService:
    def __init__(self, repository: MISRepository) -> None:
        self._repository = repository
        self._joined: tuple[tuple[Any, ...], list[list[Any]]] | None = None

    def clear_cache(self) -> None:
        """Drop the joined ledger snapshot after a table refresh."""
        self._joined = None

    # -- data ----------------------------------------------------------------

    @staticmethod
    def _repo_for(repository: MISRepository,
                  user: SessionUser | None,
                  as_of: str | None = None,
                  attribution: str = "historical") -> MISRepository:
        """Row-level role masking for the ledger."""
        if as_of is not None:
            repository = AsOfMISRepository(repository, as_of, attribution)
        if user is None or user.has_full_scope:
            return repository
        return ScopedMISRepository(repository, user)

    async def _joined_rows(self,
                           user: SessionUser | None = None,
                           as_of: str | None = None,
                           attribution: str = "historical") -> list[list[Any]]:
        """Sales detail joined with the dimension tables (cached).

        The join is rebuilt only when the repository snapshot changes (e.g.
        after a Netezza reload) or the signed-in user changes, so paging
        through a 200k-row ledger does not re-join on every request.
        """
        repo = self._repo_for(self._repository, user, as_of, attribution)
        scope = user.code if user and not user.has_full_scope else "full"
        snap = repo.snapshot()
        fingerprint = (scope, as_of, attribution) + tuple(
            (k, v["rows"], v["loaded_at"])
            for k, v in sorted(snap["tables"].items()))
        if self._joined is not None and self._joined[0] == fingerprint:
            return self._joined[1]
        rows = await self._build_joined(repo)
        self._joined = (fingerprint, rows)
        return rows

    async def _build_joined(self, repo: MISRepository) -> list[list[Any]]:
        scols, srows = await repo.get_table("MIS_FACT_SALES")
        bcols, brows = await repo.get_table("MIS_DIM_BRANCH")
        rcols, rrows = await repo.get_table("MIS_DIM_REGION")
        acols, arows = await repo.get_table("MIS_DIM_ADVISOR")
        pcols, prows = await repo.get_table("MIS_DIM_PRODUCT")
        ccols, crows = await repo.get_table("MIS_DIM_CHANNEL")

        branch_by_id = {r[bcols.index("branch_id")]: r for r in brows}
        region_by_id = {r[rcols.index("region_id")]: r for r in rrows}
        advisor_by_id = {r[acols.index("advisor_id")]: r for r in arows}
        product_by_id = {r[pcols.index("product_id")]: r for r in prows}
        channel_by_id = {r[ccols.index("channel_id")]: r for r in crows}

        si, sd, sb, sa, sp, sch, scust, samt, scom, sst = (
            scols.index("sale_id"), scols.index("sale_date"),
            scols.index("branch_id"), scols.index("advisor_id"),
            scols.index("product_id"), scols.index("channel_id"),
            scols.index("customer_id"), scols.index("amount"),
            scols.index("commission"), scols.index("sale_status"))
        bi, bn, br = (bcols.index("branch_code"), bcols.index("branch_name"),
                      bcols.index("region_id"))
        ri = rcols.index("region_code")
        ac, af, al = (acols.index("advisor_code"), acols.index("first_name"),
                      acols.index("last_name"))
        pc, pn, pg = (pcols.index("product_code"), pcols.index("product_name"),
                      pcols.index("product_group"))
        ci = ccols.index("channel_name")

        rows: list[list[Any]] = []
        for r in srows:
            b = branch_by_id.get(r[sb], ())
            a = advisor_by_id.get(r[sa], ())
            p = product_by_id.get(r[sp], ())
            region = region_by_id.get(b[br], ()) if b else ()
            rows.append([
                r[si], r[sd], b[bi] if b else "", b[bn] if b else "",
                region[ri] if region else "",
                a[ac] if a else "",
                f"{a[al]}, {a[af]}" if a else "",
                p[pg] if p else "", p[pn] if p else "",
                channel_by_id.get(r[sch], ("",))[ci],
                r[scust], r[samt], r[scom], r[sst],
            ])
        return rows

    async def filters(self) -> dict[str, list[str]]:
        """Available filter values (from the cached dimension tables)."""
        pcols, prows = await self._repository.get_table("MIS_DIM_PRODUCT")
        ccols, crows = await self._repository.get_table("MIS_DIM_CHANNEL")
        groups = sorted({r[pcols.index("product_group")] for r in prows})
        channels = [r[ccols.index("channel_name")] for r in crows]
        return {"groups": groups, "channels": channels,
                "statuses": ["BOOKED", "CANCELLED"]}

    # -- query ---------------------------------------------------------------

    async def query(self, from_: str | None, to: str | None, q: str | None,
                    group: str | None, channel: str | None, status: str | None,
                    sort: str | None, dir_: str | None,
                    page: int, page_size: int,
                    user: SessionUser | None = None,
                    as_of: str | None = None,
                    attribution: str = "historical") -> dict[str, Any]:
        rows = await self._joined_rows(user, as_of, attribution)
        total = len(rows)

        # date window
        if from_ or to:
            kept: list[list[Any]] = []
            for r in rows:
                if (not from_ or r[1] >= from_) and (not to or r[1] <= to + "-31"):
                    kept.append(r)
            rows = kept
            total = len(rows)

        # text search (customer, branch, advisor, product, channel, region)
        if q:
            needle = q.strip().lower()
            idx = {k: i for i, k in enumerate(COLUMN_KEYS)}
            text_cols = [idx[k] for k in
                         ("sale_id", "branch_code", "branch_name", "region_code",
                          "advisor_code", "advisor_name", "product_group",
                          "product_name", "channel_name", "customer_id",
                          "sale_status")]
            rows = [r for r in rows
                    if any(needle in str(r[i]).lower() for i in text_cols)]
            total = len(rows)

        # exact filters
        if group:
            gi = COLUMN_KEYS.index("product_group")
            rows = [r for r in rows if r[gi] == group]
        if channel:
            ci = COLUMN_KEYS.index("channel_name")
            rows = [r for r in rows if r[ci] == channel]
        if status:
            si = COLUMN_KEYS.index("sale_status")
            rows = [r for r in rows if r[si] == status]
        if group or channel or status:
            total = len(rows)

        # sorting (server-side); column values are never None (joined with '')
        sort = sort or "sale_date"
        dir_ = (dir_ or "desc").lower()
        if sort not in SORTABLE:
            sort = "sale_date"
        si = COLUMN_KEYS.index(sort)
        numeric = COLUMNS[si][2] != "str"
        if sort == "sale_date":
            # within the same day, newest sale id first in both directions
            rows.sort(key=lambda r: (r[1], r[0] if dir_ == "desc" else -r[0]),
                      reverse=dir_ == "desc")
        else:
            key = (lambda r: r[si]) if numeric else (lambda r: str(r[si]).lower())
            rows.sort(key=key, reverse=dir_ == "desc")

        # pagination
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(max(1, page), pages)
        start = (page - 1) * page_size
        window = rows[start:start + page_size]

        return {
            "columns": [{"key": k, "label": l, "fmt": f} for k, l, f in COLUMNS],
            "rows": window,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages,
            "filters": await self.filters(),
            "sort": sort,
            "dir": dir_,
        }

    async def export_rows(self, from_: str | None, to: str | None, q: str | None,
                          group: str | None, channel: str | None,
                          status: str | None,
                          user: SessionUser | None = None,
                          as_of: str | None = None,
                          attribution: str = "historical") -> dict[str, Any]:
        """All filtered rows for export (capped at EXPORT_CAP)."""
        rows = await self._joined_rows(user, as_of, attribution)
        if from_ or to:
            rows = [r for r in rows
                    if (not from_ or r[1] >= from_)
                    and (not to or r[1] <= to + "-31")]
        if q:
            needle = q.strip().lower()
            idx = {k: i for i, k in enumerate(COLUMN_KEYS)}
            text_cols = [idx[k] for k in
                         ("sale_id", "branch_code", "branch_name", "region_code",
                          "advisor_code", "advisor_name", "product_group",
                          "product_name", "channel_name", "customer_id",
                          "sale_status")]
            rows = [r for r in rows
                    if any(needle in str(r[i]).lower() for i in text_cols)]
        for key, value in (("product_group", group), ("channel_name", channel),
                           ("sale_status", status)):
            if value:
                i = COLUMN_KEYS.index(key)
                rows = [r for r in rows if r[i] == value]
        truncated = len(rows) > EXPORT_CAP
        return {
            "columns": [{"key": k, "label": l, "fmt": f} for k, l, f in COLUMNS],
            "rows": rows[:EXPORT_CAP] if truncated else rows,
            "truncated": truncated,
            "total": len(rows),
        }
