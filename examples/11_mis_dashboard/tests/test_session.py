"""
Tests for session persona resolution (role-based access) — over the seeded
in-memory dataset, no database required.

Covers the user directory, switching, and the PeopleService scope rules for
every role: ANALYST (everything), AREA_MANAGER (one region), BRANCH_MANAGER
(one branch), ADVISOR (themselves + their branch).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fakes import FakeRepository  # noqa: E402
from seed import build_dataset  # noqa: E402
from app.repositories import ScopedMISRepository  # noqa: E402
from app.services.ledger_service import LedgerService  # noqa: E402
from app.services.people_service import Forbidden, PeopleService  # noqa: E402
from app.services.report_service import ReportService  # noqa: E402
from app.services.session_service import SessionService, UnknownUser  # noqa: E402
from app.core.roles import SessionUser  # noqa: E402


@pytest.fixture(scope="module")
def dataset():
    return build_dataset(scale=1)


@pytest.fixture()
def repo(dataset) -> FakeRepository:
    return FakeRepository(dataset)


@pytest.fixture()
def session(repo: FakeRepository) -> SessionService:
    return SessionService(repo)


@pytest.fixture()
def people(repo: FakeRepository) -> PeopleService:
    return PeopleService(repo, ttl=60)


# ---------------------------------------------------------------------------
# Session service
# ---------------------------------------------------------------------------

async def test_default_user_is_analyst(session: SessionService) -> None:
    me = await session.current()
    assert me.code == "NET01" and me.role == "ANALYST"


async def test_user_directory(session: SessionService) -> None:
    users = await session.users()
    roles = {u["role"] for u in users}
    assert {"ANALYST", "AREA_MANAGER", "BRANCH_MANAGER", "ADVISOR"} <= roles
    assert {"MIS_SQL_DEVELOPER", "NETWORK_HEAD", "REGIONAL_DIRECTOR",
            "BRANCH_DIRECTOR", "CUSTOMER_ADVISOR", "HQ_FULL_ACCESS",
            "APP_TESTER"} <= roles
    assert any(u["code"] == "BM_DUB01" for u in users)
    assert any(u["code"] == "ADV_P0001" for u in users)


async def test_switch_resolves_without_global_state(session: SessionService) -> None:
    user = await session.switch("ADV_P0001")
    assert user.role == "ADVISOR" and user.advisor_id == 1
    # A request must never change the effective user for another request.
    assert (await session.current()).code == "NET01"
    assert (await session.resolve("ADV_P0001")).code == user.code

    bm = await session.switch("BM_DUB01")
    assert bm.role == "BRANCH_MANAGER" and bm.branch_id == 15  # DUB01
    assert bm.region_id == 3


async def test_switch_unknown(session: SessionService) -> None:
    with pytest.raises(UnknownUser):
        await session.switch("NOPE00")


# ---------------------------------------------------------------------------
# ADVISOR scope: themselves + their branch only
# ---------------------------------------------------------------------------

@pytest.fixture()
async def advisor_user(session: SessionService) -> SessionUser:
    return await session.switch("ADV_P0001")  # P0001 works in BEL01 (branch 1)


async def test_advisor_sees_only_own_branch_and_self(people: PeopleService,
                                                     advisor_user: SessionUser) -> None:
    branches = await people.branch_list(advisor_user)
    assert [b["code"] for b in branches] == ["BEL01"]
    advisors = await people.advisor_list(advisor_user)
    assert [a["code"] for a in advisors] == ["P0001"]


async def test_advisor_cumulative_own_ok_foreign_forbidden(
        people: PeopleService, advisor_user: SessionUser) -> None:
    await people.cumulative("branch", "BEL01", "2026-08", advisor_user)
    await people.cumulative("advisor", "P0001", "2026-08", advisor_user)
    with pytest.raises(Forbidden):
        await people.cumulative("branch", "DUB01", "2026-08", advisor_user)
    with pytest.raises(Forbidden):
        await people.cumulative("advisor", "P0002", "2026-08", advisor_user)


async def test_advisor_panel_own_ok_foreign_forbidden(
        people: PeopleService, advisor_user: SessionUser) -> None:
    await people.advisor_panel("P0001", advisor_user)
    with pytest.raises(Forbidden):
        await people.advisor_panel("P0002", advisor_user)


# ---------------------------------------------------------------------------
# BRANCH_MANAGER scope: one branch + its advisors
# ---------------------------------------------------------------------------

@pytest.fixture()
async def manager_user(session: SessionService) -> SessionUser:
    return await session.switch("BM_BEL01")


async def test_manager_sees_only_his_branch(people: PeopleService,
                                            manager_user: SessionUser) -> None:
    branches = await people.branch_list(manager_user)
    assert [b["code"] for b in branches] == ["BEL01"]
    advisors = await people.advisor_list(manager_user)
    assert advisors and all(a["branch_code"] == "BEL01" for a in advisors)
    # BEL01 has 4-5 advisors at scale=1 — more than just himself
    assert len(advisors) > 1


async def test_manager_cumulative_scope(people: PeopleService,
                                        manager_user: SessionUser) -> None:
    await people.cumulative("branch", "BEL01", "2026-08", manager_user)
    # advisors of his branch are visible
    advisors = await people.advisor_list(manager_user)
    await people.cumulative("advisor", advisors[0]["code"], "2026-08",
                            manager_user)
    # advisors of other branches are not
    other = next(a for a in await people.advisor_list(None)
                 if a["branch_code"] != "BEL01")
    with pytest.raises(Forbidden):
        await people.cumulative("advisor", other["code"], "2026-08",
                                manager_user)
    with pytest.raises(Forbidden):
        await people.cumulative("branch", "DUB01", "2026-08", manager_user)


# ---------------------------------------------------------------------------
# AREA_MANAGER scope: one region
# ---------------------------------------------------------------------------

@pytest.fixture()
async def area_user(session: SessionService) -> SessionUser:
    return await session.switch("AM_RNOR")  # North Region (region 1)


async def test_area_manager_sees_region_only(people: PeopleService,
                                             area_user: SessionUser) -> None:
    branches = await people.branch_list(area_user)
    assert 1 < len(branches) < 41
    assert all(b["region_name"] == "North Region" for b in branches)
    advisors = await people.advisor_list(area_user)
    assert advisors and all(a["region_name"] == "North Region" for a in advisors)


async def test_area_manager_cumulative_scope(people: PeopleService,
                                             area_user: SessionUser) -> None:
    branches = await people.branch_list(area_user)
    await people.cumulative("branch", branches[0]["code"], "2026-08", area_user)
    advisors = await people.advisor_list(area_user)
    await people.advisor_panel(advisors[0]["code"], area_user)
    with pytest.raises(Forbidden):
        await people.cumulative("branch", "DUB01", "2026-08", area_user)


# ---------------------------------------------------------------------------
# ANALYST scope: everything (and default when no user passed)
# ---------------------------------------------------------------------------

async def test_analyst_and_no_user_see_everything(people: PeopleService) -> None:
    branches = await people.branch_list(None)
    assert len(branches) == 41
    assert len(await people.advisor_list(None)) > 100
    await people.cumulative("branch", "DUB01", "2026-08", None)
    await people.advisor_panel("P0001", None)


async def test_named_demo_personas_use_the_same_scope_policy(
        people: PeopleService, session: SessionService) -> None:
    regional = await session.resolve("REG_DIR_RNOR")
    regional_branches = await people.branch_list(regional)
    assert regional.role == "REGIONAL_DIRECTOR"
    assert regional_branches and all(
        branch["region_name"] == "North Region" for branch in regional_branches)

    branch = await session.resolve("BR_DIR_BEL01")
    assert [row["code"] for row in await people.branch_list(branch)] == ["BEL01"]

    advisor = await session.resolve("ADV_DEMO_P0001")
    assert [row["code"] for row in await people.advisor_list(advisor)] == ["P0001"]


# ---------------------------------------------------------------------------
# Row-level masking of the MIS report pages (ScopedMISRepository)
# ---------------------------------------------------------------------------

async def test_scoped_repo_filters_facts_by_role(dataset, repo: FakeRepository,
                                                 session: SessionService) -> None:
    scols, srows = dataset["MIS_FACT_SALES"]
    bi, ai = scols.index("branch_id"), scols.index("advisor_id")

    bm = await session.switch("BM_BEL01")  # branch 1
    scoped = ScopedMISRepository(repo, bm)
    cols, rows = await scoped.get_table("MIS_FACT_SALES")
    assert rows and all(r[bi] == 1 for r in rows)
    assert len(rows) < len(srows)
    # advisor perf limited to BEL01 advisors
    pcols, prows = await scoped.get_table("MIS_FACT_ADVISOR_PERF")
    bel01_advs = {r[ai] for r in srows if r[bi] == 1}
    assert prows and all(r[pcols.index("advisor_id")] in bel01_advs for r in prows)
    # dimensions pass through unfiltered
    dcols, drows = await scoped.get_table("MIS_DIM_PRODUCT")
    assert len(drows) == len(dataset["MIS_DIM_PRODUCT"][1])

    adv = await session.switch("ADV_P0001")
    scoped = ScopedMISRepository(repo, adv)
    cols, rows = await scoped.get_table("MIS_FACT_SALES")
    assert rows and all(r[ai] == 1 for r in rows)  # own sales only
    bcols, brows = await scoped.get_table("MIS_FACT_BALANCES")
    assert brows and all(r[bcols.index("branch_id")] == 1 for r in brows)

    am = await session.switch("AM_RNOR")
    scoped = ScopedMISRepository(repo, am)
    cols, rows = await scoped.get_table("MIS_FACT_SALES")
    region1 = {r[0] for r in dataset["MIS_DIM_BRANCH"][1]
               if r[dataset["MIS_DIM_BRANCH"][0].index("region_id")] == 1}
    assert rows and all(r[bi] in region1 for r in rows)


async def test_report_payloads_are_scoped_per_user(repo: FakeRepository,
                                                   session: SessionService) -> None:
    service = ReportService(repo, ttl=60)
    analyst = await session.switch("NET01")
    manager = await session.switch("BM_BEL01")

    full = await service.build("loans", None, None, "branch", analyst)
    scoped = await service.build("loans", None, None, "branch", manager)
    full_total = sum(r[3] for r in full["synthetic"]["rows"])
    scoped_total = sum(r[3] for r in scoped["synthetic"]["rows"])
    assert scoped_total > 0 and scoped_total < full_total
    # analytic rows contain only BEL01
    assert {r[0] for r in scoped["analytic"]["rows"]} == {"BEL01"}
    assert len({r[0] for r in full["analytic"]["rows"]}) > 1

    # payload cache is per user: repeat calls reuse the same scope
    calls = repo.load_count
    again = await service.build("loans", None, None, "branch", manager)
    assert repo.load_count == calls
    assert again["synthetic"]["rows"] == scoped["synthetic"]["rows"]


async def test_scoped_drill_and_ledger(repo: FakeRepository,
                                       session: SessionService) -> None:
    manager = await session.switch("BM_BEL01")
    service = ReportService(repo, ttl=60)

    # drilling sales of a foreign advisor returns nothing (masked)
    out = await service.drill("loans", "sales", "P0100", None, None, manager)
    assert not out["rows"]
    # own branch advisors still drill fine
    out = await service.drill("loans", "sales", "P0001", None, None, manager)
    assert out["rows"]

    ledger = LedgerService(repo)
    full = await ledger.query(None, None, None, None, None, None, None, None, 1, 25)
    scoped = await ledger.query(None, None, None, None, None, None, None, None,
                                1, 25, manager)
    assert 0 < scoped["total"] < full["total"]
    cols = scoped["columns"]
    bi = next(i for i, c in enumerate(cols) if c["key"] == "branch_code")
    assert {r[bi] for r in scoped["rows"]} == {"BEL01"}

    # exports respect the scope too
    exported = await ledger.export_rows(None, None, None, None, None, None, manager)
    assert exported["total"] == scoped["total"]
