"""Capture screenshots of LaunchPad pages using Playwright.

Usage: python scripts/capture_screenshots.py [--profile-id 1]

Requires the server to be running on localhost:7070.
Saves PNGs to docs/screenshots/.
"""

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

BASE_URL = "http://localhost:7070"
OUT_DIR = Path(__file__).parent.parent / "docs" / "screenshots"
PROFILE_ID = int(sys.argv[sys.argv.index("--profile-id") + 1]) if "--profile-id" in sys.argv else 1

PAGES = [
    ("login", None, "Login screen"),
    ("dashboard", "dashboard", "Dashboard"),
    ("pipeline", "pipeline", "Pipeline Board"),
    ("listings", "listings", "All Listings"),
    ("scanner", "scanner", "Portal Scanner"),
    ("gmail", "gmail", "Gmail Integration"),
    ("settings", "settings", "Settings"),
    ("companies", "companies", "Company Research"),
    ("interview", "interview", "Interview Prep"),
]


async def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=2,
        )
        page = await context.new_page()

        # 1. Capture login screen
        await page.goto(BASE_URL)
        await page.wait_for_timeout(2000)
        await page.screenshot(path=str(OUT_DIR / "login.png"), full_page=False)
        print(f"✓ login.png")

        # 2. Log in by clicking the profile
        try:
            # Click the first profile card
            profile_card = page.locator(".login-profile").first
            await profile_card.click()
            await page.wait_for_timeout(1000)

            # If there's a PIN prompt, try empty submit
            pin_input = page.locator("input[type='password']")
            if await pin_input.count() > 0:
                await pin_input.fill("")
                submit = page.locator("button:has-text('Login'), button:has-text('Enter')")
                if await submit.count() > 0:
                    await submit.first.click()
                    await page.wait_for_timeout(1000)
        except Exception as e:
            print(f"  Login attempt: {e}")

        # Alternative: use API to login directly
        await page.evaluate(f"""
            async () => {{
                try {{
                    await fetch('/api/auth/login', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        credentials: 'include',
                        body: JSON.stringify({{profile_id: {PROFILE_ID}, pin: null}})
                    }});
                }} catch(e) {{}}
            }}
        """)
        await page.goto(BASE_URL)
        await page.wait_for_timeout(3000)

        # 3. Capture each page
        for filename, page_name, label in PAGES:
            if page_name is None:
                continue  # login already captured
            try:
                # Navigate via the JS router
                await page.evaluate(f"showPage('{page_name}')")
                await page.wait_for_timeout(2000)
                await page.screenshot(path=str(OUT_DIR / f"{filename}.png"), full_page=False)
                print(f"✓ {filename}.png — {label}")
            except Exception as e:
                print(f"✗ {filename}.png — {e}")

        # 4. Mobile view of dashboard
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.evaluate("showPage('dashboard')")
        await page.wait_for_timeout(2000)
        await page.screenshot(path=str(OUT_DIR / "mobile.png"), full_page=False)
        print(f"✓ mobile.png — Mobile Dashboard")

        await browser.close()

    print(f"\nAll screenshots saved to {OUT_DIR}/")


if __name__ == "__main__":
    asyncio.run(main())
