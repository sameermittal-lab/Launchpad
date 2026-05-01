#!/usr/bin/env python3
"""Seed a demo profile with fake data and capture screenshots.

Creates a Jane Doe profile with sample listings, companies, and usage data,
then uses Playwright to capture screenshots of each page.

Usage:
    python3 scripts/seed_demo_and_screenshot.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

_THIS = Path(__file__).resolve()
_ROOT = _THIS.parent.parent
sys.path.insert(0, str(_ROOT))

from app.database import SessionLocal, init_db
from app.models import Listing, Profile, TrackedCompany, Usage, HistoryEvent


DEMO_LISTINGS = [
    {"company": "Anthropic", "role_title": "Director of Product, Claude Platform", "status": "evaluated", "score": 4.6, "grade": "A", "source": "manual", "location": "San Francisco, CA", "job_type": "Hybrid"},
    {"company": "OpenAI", "role_title": "Principal Product Manager, API Platform", "status": "evaluated", "score": 4.3, "grade": "A", "source": "scanner", "location": "San Francisco, CA", "job_type": "Onsite"},
    {"company": "Stripe", "role_title": "Staff PM, ML Infrastructure", "status": "evaluated", "score": 4.1, "grade": "A-", "source": "ai_monitor", "location": "Seattle, WA", "job_type": "Remote"},
    {"company": "Databricks", "role_title": "Director of Product, Data AI", "status": "applied", "score": 4.4, "grade": "A", "source": "gmail", "location": "San Francisco, CA", "job_type": "Hybrid"},
    {"company": "Scale AI", "role_title": "Principal PM, Data Engine", "status": "interview", "score": 4.2, "grade": "A-", "source": "scanner", "location": "San Francisco, CA", "job_type": "Onsite"},
    {"company": "Figma", "role_title": "Director of Product, AI Features", "status": "evaluated", "score": 3.8, "grade": "B+", "source": "ai_monitor", "location": "San Francisco, CA", "job_type": "Hybrid"},
    {"company": "Notion", "role_title": "Staff PM, AI & Automation", "status": "new", "score": None, "grade": None, "source": "scanner", "location": "New York, NY", "job_type": "Hybrid"},
    {"company": "Vercel", "role_title": "Principal PM, Developer Platform", "status": "new", "score": None, "grade": None, "source": "ai_monitor", "location": "Remote", "job_type": "Remote"},
    {"company": "Snowflake", "role_title": "Director, Product Management - AI/ML", "status": "evaluated", "score": 3.5, "grade": "B", "source": "scanner", "location": "Bellevue, WA", "job_type": "Hybrid"},
    {"company": "Datadog", "role_title": "Staff PM, AI Observability", "status": "passed", "score": 3.9, "grade": "B+", "source": "gmail", "location": "New York, NY", "job_type": "Hybrid", "pass_reason": "comp_too_low"},
    {"company": "MongoDB", "role_title": "Principal PM, Atlas Vector Search", "status": "evaluated", "score": 4.0, "grade": "A-", "source": "scanner", "location": "Seattle, WA", "job_type": "Remote"},
    {"company": "Confluent", "role_title": "Director of Product, Stream Processing", "status": "new", "score": None, "grade": None, "source": "ai_monitor", "location": "Remote", "job_type": "Remote"},
]

DEMO_COMPANIES = [
    {"name": "Anthropic", "careers_url": "https://jobs.ashbyhq.com/anthropic", "platform": "ashby", "enabled": True},
    {"name": "OpenAI", "careers_url": "https://openai.com/careers", "platform": "custom", "enabled": True},
    {"name": "Stripe", "careers_url": "https://stripe.com/jobs", "platform": "custom", "enabled": True},
    {"name": "Databricks", "careers_url": "https://boards.greenhouse.io/databricks", "platform": "greenhouse", "enabled": True},
    {"name": "Scale AI", "careers_url": "https://jobs.ashbyhq.com/scale", "platform": "ashby", "enabled": True},
    {"name": "Figma", "careers_url": "https://boards.greenhouse.io/figma", "platform": "greenhouse", "enabled": True},
    {"name": "Notion", "careers_url": "https://boards.greenhouse.io/notion", "platform": "greenhouse", "enabled": True},
    {"name": "Snowflake", "careers_url": "https://snowflake.wd5.myworkdayjobs.com/en-US/snowflake", "platform": "workday", "enabled": True},
    {"name": "NVIDIA", "careers_url": "https://nvidia.wd5.myworkdayjobs.com", "platform": "workday", "enabled": True, "ai_monitor_enabled": True},
]

DEMO_USAGE = [
    {"action": "evaluation", "provider": "openai", "model": "gpt-4o", "cost": 0.042, "tokens": 3200},
    {"action": "evaluation", "provider": "openai", "model": "gpt-4o", "cost": 0.038, "tokens": 2900},
    {"action": "evaluation", "provider": "openai", "model": "gpt-4o", "cost": 0.045, "tokens": 3400},
    {"action": "evaluation", "provider": "openai", "model": "gpt-4o", "cost": 0.041, "tokens": 3100},
    {"action": "evaluation", "provider": "openai", "model": "gpt-4o", "cost": 0.039, "tokens": 2950},
    {"action": "resume_tailor", "provider": "openai", "model": "gpt-4o", "cost": 0.035, "tokens": 2600},
    {"action": "resume_tailor", "provider": "openai", "model": "gpt-4o", "cost": 0.032, "tokens": 2400},
    {"action": "cover_letter", "provider": "openai", "model": "gpt-4o", "cost": 0.022, "tokens": 1700},
    {"action": "company_research", "provider": "openai", "model": "gpt-4o", "cost": 0.065, "tokens": 4800},
    {"action": "company_research", "provider": "openai", "model": "gpt-4o", "cost": 0.058, "tokens": 4200},
    {"action": "ai_monitor_gemini_search", "provider": "google", "model": "gemini-2.5-flash", "cost": 0.012, "tokens": 900},
    {"action": "ai_monitor_gemini_search", "provider": "google", "model": "gemini-2.5-flash", "cost": 0.011, "tokens": 850},
    {"action": "smart_title_filter", "provider": "openai", "model": "gpt-4o", "cost": 0.003, "tokens": 250},
    {"action": "query_planner", "provider": "openai", "model": "gpt-4o", "cost": 0.028, "tokens": 2100},
]


def seed_demo():
    init_db()
    db = SessionLocal()

    # Check if demo profile exists
    existing = db.query(Profile).filter(Profile.name == "Jane Doe").first()
    if existing:
        profile = existing
        print(f"Jane Doe profile exists (id={profile.id}), adding demo data...")
    else:
        from scripts.seed_demo_profile import seed
        pid = seed()
        profile = db.query(Profile).get(pid)

    # Clear old demo listings for this profile
    db.query(Listing).filter(Listing.profile_id == profile.id).delete()
    db.query(TrackedCompany).filter(TrackedCompany.profile_id == profile.id).delete()
    db.query(Usage).filter(Usage.profile_id == profile.id).delete()
    db.query(HistoryEvent).filter(HistoryEvent.profile_id == profile.id).delete()
    db.commit()

    # Seed listings
    now = datetime.utcnow()
    for i, l in enumerate(DEMO_LISTINGS):
        listing = Listing(
            profile_id=profile.id,
            company=l["company"],
            role_title=l["role_title"],
            status=l["status"],
            score=l.get("score"),
            grade=l.get("grade"),
            source=l["source"],
            location=l.get("location"),
            job_type=l.get("job_type"),
            url=f"https://example.com/jobs/{i+1}",
            created_at=now - timedelta(days=i),
            pass_reason=l.get("pass_reason"),
            passed_at=now if l.get("pass_reason") else None,
            use_for_calibration=bool(l.get("pass_reason")),
            ai_summary=f"Strong match for Jane's AI platform background. {l['company']} offers significant growth potential." if l.get("score") else None,
        )
        db.add(listing)

    # Seed companies
    for c in DEMO_COMPANIES:
        company = TrackedCompany(
            profile_id=profile.id,
            name=c["name"],
            careers_url=c["careers_url"],
            platform=c["platform"],
            enabled=c["enabled"],
            ai_monitor_enabled=c.get("ai_monitor_enabled", False),
            last_scanned_at=now - timedelta(hours=6),
            last_job_count=15,
        )
        db.add(company)

    # Seed usage
    for j, u in enumerate(DEMO_USAGE):
        usage = Usage(
            profile_id=profile.id,
            action=u["action"],
            provider=u["provider"],
            model=u["model"],
            cost_usd=u["cost"],
            prompt_tokens=u["tokens"],
            completion_tokens=u["tokens"] // 3,
            created_at=now - timedelta(hours=j * 2),
        )
        db.add(usage)

    db.commit()
    print(f"✓ Seeded {len(DEMO_LISTINGS)} listings, {len(DEMO_COMPANIES)} companies, {len(DEMO_USAGE)} usage records")
    db.close()
    return profile.id


def capture_screenshots(profile_id: int):
    import asyncio
    from playwright.async_api import async_playwright

    OUT_DIR = _ROOT / "docs" / "screenshots"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            ctx = await browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
            page = await ctx.new_page()

            # Login via API
            await page.goto("http://localhost:7070")
            await page.wait_for_timeout(1500)

            # Capture login screen first
            await page.screenshot(path=str(OUT_DIR / "login.png"), full_page=False)
            print("✓ login.png")

            # Login
            await page.evaluate(f"""
                async () => {{
                    await fetch('/api/auth/login', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        credentials: 'include',
                        body: JSON.stringify({{profile_id: {profile_id}, pin: null}})
                    }});
                }}
            """)
            await page.goto("http://localhost:7070")
            await page.wait_for_timeout(3000)

            pages_to_capture = [
                ("dashboard", "Dashboard"),
                ("pipeline", "Pipeline Board"),
                ("listings", "All Listings"),
                ("scanner", "Portal Scanner"),
                ("settings", "Settings"),
            ]

            for name, label in pages_to_capture:
                await page.evaluate(f"showPage('{name}')")
                await page.wait_for_timeout(2000)
                await page.screenshot(path=str(OUT_DIR / f"{name}.png"), full_page=False)
                print(f"✓ {name}.png — {label}")

            # Mobile
            await page.set_viewport_size({"width": 390, "height": 844})
            await page.evaluate("showPage('dashboard')")
            await page.wait_for_timeout(2000)
            await page.screenshot(path=str(OUT_DIR / "mobile.png"), full_page=False)
            print("✓ mobile.png — Mobile Dashboard")

            await browser.close()
        print(f"\nAll screenshots saved to {OUT_DIR}/")

    asyncio.run(run())


if __name__ == "__main__":
    pid = seed_demo()
    capture_screenshots(pid)
