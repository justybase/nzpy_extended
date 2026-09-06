#!/usr/bin/env python3
"""Browser smoke checks for the three decision-reporting views.

Usage:
    python tools/visual_check.py [--base http://127.0.0.1:8482]
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8482")
    parser.add_argument("--out", default="tools/screenshots")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    def check(name: str, condition: bool) -> None:
        print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
        if not condition:
            failures.append(name)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(args.base, wait_until="networkidle")
        page.wait_for_selector(".status-card", state="attached")
        check("control tower renders", page.locator(".status-card").count() == 4)
        check("exception rows render", page.locator(".exception-row").count() > 0)
        check("network has narrative", page.locator(".narrative").count() == 3)
        check("no desktop overflow", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"))
        page.screenshot(path=str(out / "01_control_tower.png"))

        page.click(".exception-row")
        page.wait_for_selector(".bridge", state="attached")
        check("driver bridge renders", page.locator(".bridge-item").count() > 2)
        check("driver evidence renders", page.locator("table tbody tr").count() > 0)
        page.screenshot(path=str(out / "02_driver_analysis.png"))

        page.click("[data-page='review']")
        page.wait_for_selector(".review-page", state="attached")
        check("review pack renders", page.locator(".decision").count() == 1)
        check("review actions render", page.locator(".action-list li").count() > 0)
        page.emulate_media(media="print")
        pdf = page.pdf(prefer_css_page_size=True, print_background=True)
        (out / "03_review_pack.pdf").write_bytes(pdf)
        check("print pack is one page", len(re.findall(rb"/Type\s*/Page\b", pdf)) == 1)
        page.emulate_media(media="screen")
        page.screenshot(path=str(out / "03_review_pack.png"))

        for width in (768, 390):
            page.set_viewport_size({"width": width, "height": 844})
            page.goto(args.base, wait_until="networkidle")
            page.wait_for_selector(".status-card", state="attached")
            check(f"{width}px no overflow", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"))
            page.screenshot(path=str(out / f"04_control_tower_{width}.png"))
        browser.close()

    if failures:
        print("Failed checks:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"All visual checks passed. Screenshots saved to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
