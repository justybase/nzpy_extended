#!/usr/bin/env python3
"""
Browser-based visual check — catches layout regressions in the dashboard UI.

Runs a real headless Chromium (Playwright) against the running app and checks
layout invariants on the pages that changed most recently:

    - overview page: renders without horizontal overflow
    - daily brief: KPI cards, network strip, chart canvas, day table;
      the print emulation hides the sidebar
    - role switcher: "Signed in as" selector switches users; pickers lock to
      the allowed scope; the badge updates

Screenshots are saved to tools/screenshots/ for manual review. Any failed
assertion exits with a non-zero code and a message — the screenshots are
still written so you can see what happened.

Usage:
    python tools/visual_check.py [--base http://127.0.0.1:8481] [--out tools/screenshots]
    pip install playwright && python -m playwright install chromium
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(f"{name}: {detail}")


def no_horizontal_overflow(page) -> bool:
    return page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth + 1")


def visible(page, selector: str) -> bool:
    el = page.query_selector(selector)
    if el is None:
        return False
    box = el.bounding_box()
    return box is not None and box["width"] > 0 and box["height"] > 0


def wait_for(page, selector: str, timeout: int = 15000) -> bool:
    """Wait for the element to exist in the DOM.

    Uses state="attached" on purpose: <option> elements inside a closed
    <select> dropdown are never "visible" even though they are rendered.
    """
    try:
        page.wait_for_selector(selector, state="attached", timeout=timeout)
        return True
    except Exception:
        return False


def open_menu(page, label: str) -> bool:
    page.click(f"#menu .menu-item:has-text('{label}')")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8481")
    ap.add_argument("--out", default="tools/screenshots")
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        # ------------------------------------------------------------ overview
        print("Overview page")
        page.goto(args.base, wait_until="networkidle")
        check("overview loads", wait_for(page, "#kpis .kpi"))
        check("overview no horizontal overflow", no_horizontal_overflow(page))
        check("period selectors have labels",
              page.locator("#select-from option").first.text_content()
              and page.locator("#select-to option").first.text_content(),
              "month labels are missing")
        check("overview has decision insight area",
              wait_for(page, "#insights") and page.locator("#insights .insight").count() >= 1)
        check("overview has plan KPI",
              page.locator("#kpis .kpi-label").all_text_contents()
              and any("plan" in text.lower() for text in page.locator("#kpis .kpi-label").all_text_contents()))
        page.screenshot(path=str(out_dir / "01_overview.png"))

        # ------------------------------------------------------------ brief
        print("Daily brief page")
        open_menu(page, "Daily brief")
        check("brief hides report-only toolbar",
              page.is_hidden("#dim-label") and page.is_hidden("#btn-charts-pdf"))
        check("brief renders", wait_for(page, ".brief .brief-kpis .kpi"))
        check("brief has 6 KPI cards",
              page.locator(".brief .brief-kpis .kpi").count() == 6,
              f"count={page.locator('.brief .brief-kpis .kpi').count()}")
        check("network KPI strip visible", visible(page, ".brief-net"))
        canvas = page.query_selector("#brief-canvas")
        cbox = canvas.bounding_box() if canvas else None
        check("brief chart canvas rendered",
              cbox is not None and cbox["width"] > 100 and cbox["height"] > 50)
        check("brief day table has rows",
              page.locator("#brief-table tbody tr").count() >= 28,
              f"rows={page.locator('#brief-table tbody tr').count()}")
        check("brief no horizontal overflow", no_horizontal_overflow(page))
        page.screenshot(path=str(out_dir / "02_brief.png"))

        # print emulation — sidebar must disappear, brief stays
        page.emulate_media(media="print")
        check("print: sidebar hidden",
              not visible(page, "#sidebar") or page.is_hidden("#sidebar"))
        check("print: brief visible", visible(page, ".brief"))
        page.screenshot(path=str(out_dir / "03_brief_print.png"))
        page.emulate_media(media="screen")

        # --------------------------------------------------- role switcher
        print("Role switcher")
        open_menu(page, "My results")
        check("picker populates",
              wait_for(page, "#entity-select option"), "no options rendered")
        check("picker has many advisors as analyst",
              page.locator("#entity-select option").count() > 100,
              f"count={page.locator('#entity-select option').count()}")
        check("user selector present",
              page.locator("#user-select option").count() >= 10,
              f"count={page.locator('#user-select option').count()}")

        # switch to an advisor user -> picker must lock to a single option
        page.select_option("#user-select", "ADV_P0001")
        page.wait_for_timeout(700)
        badge = page.text_content("#user-badge") or ""
        check("badge shows the advisor role", "Advisor" in badge, badge)
        opts = page.locator("#entity-select option").count()
        check("advisor picker locked to one option", opts == 1, f"count={opts}")
        check("advisor picker disabled", page.is_disabled("#entity-select"))
        page.screenshot(path=str(out_dir / "04_role_advisor.png"))

        # switch to a branch manager -> picker lists only their branch
        page.select_option("#user-select", "BM_DUB01")
        page.wait_for_timeout(700)
        badge = page.text_content("#user-badge") or ""
        check("badge shows the manager role", "Branch manager" in badge, badge)
        opts = page.locator("#entity-select option").count()
        check("manager picker has only his branch advisors", 1 <= opts <= 10,
              f"count={opts}")
        page.screenshot(path=str(out_dir / "05_role_manager.png"))

        # report page is masked + subtitle carries the scope note
        open_menu(page, "Loans")
        page.wait_for_timeout(700)
        check("report URL keeps context", "page=loans" in page.url)
        subtitle = page.text_content("#page-subtitle") or ""
        check("report subtitle notes the scope",
              "scoped to your Branch manager" in subtitle, subtitle)
        codes = page.eval_on_selector_all(
            "#analytic-table tbody tr td:first-child",
            "els => els.map(e => e.textContent)")
        check("analyst table shows only his branch",
              codes and set(codes) <= {"DUB01"}, str(sorted(set(codes))[:5]))
        page.screenshot(path=str(out_dir / "06_report_scoped.png"))

        # back to the analyst
        page.select_option("#user-select", "NET01")
        page.wait_for_timeout(700)
        badge = page.text_content("#user-badge") or ""
        check("badge back to analyst", "Network analyst" in badge, badge)

        browser.close()

    if FAILURES:
        print(f"\n{len(FAILURES)} visual check(s) FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        print(f"\nScreenshots saved to {out_dir}/")
        return 1
    print(f"\nAll visual checks passed. Screenshots saved to {out_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
