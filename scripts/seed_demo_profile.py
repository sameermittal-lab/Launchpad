#!/usr/bin/env python3
"""Seed a demo "Jane Doe" profile so a fresh install has something to click on.

Safe to run multiple times — no-ops if a Jane Doe profile already exists.

Usage:
    python3 launchpad/scripts/seed_demo_profile.py

What it creates:
    * Profile "Jane Doe" (role: Staff Product Manager, AI Platforms) with:
        - target_roles, target_salary, target_locations
        - cv.md under users/{profile_id}/cv.md
        - default scoring weights + trusted senders + AI monitor defaults
    * NO listings, NO API keys, NO Gmail accounts — those are user-supplied.

You can delete the demo profile from the Settings page.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow the script to run from the launchpad root without a package install.
_THIS = Path(__file__).resolve()
_LAUNCHPAD_ROOT = _THIS.parent.parent
sys.path.insert(0, str(_LAUNCHPAD_ROOT))

from app.config import settings  # noqa: E402
from app.database import db_session, init_db  # noqa: E402
from app.models import Profile  # noqa: E402


JANE_DOE_CV = """# Jane Doe

Email: jane.doe@example.com
Phone: +1 (555) 555-0123
Location: Seattle, WA (open to remote)
LinkedIn: linkedin.com/in/janedoe-example

## Summary

Staff Product Manager with 10+ years of experience building AI-powered platforms
and developer tools at enterprise scale. Shipped the inference gateway behind
a flagship AI product, grew a self-serve API line of business from $0 to $20M ARR,
and led cross-functional teams of 15+ engineers, designers, and researchers.
Looking for a Director or Principal PM role at a company on the AI-infrastructure
or AI-applications S-curve.

## Experience

### Staff Product Manager · ExampleCorp AI Platform
_2023 - Present_

- Led roadmap for the AI inference platform serving 50+ internal teams and 3
  external products. Reduced p99 latency 40% while cutting GPU spend 22%.
- Defined the vision and shipped a vector-search primitive that became the
  foundation for the company's RAG strategy; now used by 12 product teams.
- Partnered with research to productionize a fine-tuned open-weights model,
  cutting cost-per-query 60% vs. frontier-API baseline.

### Senior Product Manager · ExampleCorp Developer Tools
_2020 - 2023_

- Grew the public API from 500 to 18,000 monthly active developers.
- Led the migration of the CLI from Python to Go, improving startup time 10x.
- Shipped the first paid tier; converted 8% of free users in the first quarter.

### Product Manager · StartupXYZ
_2017 - 2020_

- Owned the data pipeline product from 0 to 1; acquired by a public company.
- Early PM on the feature flag system, the source of the eventual platform team.

## Education

- B.S. Computer Science, Example University, 2014

## Skills

Product strategy, 0-to-1 launches, AI/ML platforms, developer tools, platform
PLG, enterprise sales enablement, technical architecture reviews, pricing,
cross-functional leadership.
"""


JANE_DOE_PROFILE_DATA = {
    "email": "jane.doe@example.com",
    "phone": "+1 (555) 555-0123",
    "linkedin": "https://linkedin.com/in/janedoe-example",
    "location": "Seattle, WA",
    "target_locations": ["Seattle, WA", "Remote (US)", "San Francisco Bay Area"],
    "target_roles": [
        "Director of Product (AI platforms / developer tools)",
        "Principal Product Manager (ML platform)",
        "Staff Product Manager (AI infrastructure)",
    ],
    "target_salary": "$325k - $450k total comp",
}


def seed() -> int:
    """Create the demo profile. Returns profile_id (existing or new). Idempotent."""
    init_db()
    with db_session() as db:
        existing = (
            db.query(Profile)
            .filter(Profile.name == "Jane Doe")
            .first()
        )
        if existing is not None:
            print(f"Jane Doe profile already exists (id={existing.id}). No changes.")
            return existing.id

        profile = Profile(
            name="Jane Doe",
            role_title="Staff Product Manager, AI Platforms",
            profile_data=JANE_DOE_PROFILE_DATA,
            # Leave everything else at the model defaults:
            #   - no LLM API key (user adds their own)
            #   - no PIN
            #   - default scoring weights, scan interval, trusted senders, etc.
        )
        db.add(profile)
        db.flush()
        profile_id = profile.id

        # Write cv.md so the resume management page has something to show
        cv_dir = settings.resolved_data_dir / str(profile_id)
        cv_dir.mkdir(parents=True, exist_ok=True)
        (cv_dir / "cv.md").write_text(JANE_DOE_CV, encoding="utf-8")

        print(f"\u2713 Created Jane Doe profile (id={profile_id})")
        print(f"  CV written to: {cv_dir / 'cv.md'}")
        print()
        print("Next steps:")
        print("  1. Start the server (./start.sh or start.bat)")
        print("  2. Pick 'Jane Doe' on the login screen")
        print("  3. Go to Settings -> LLM and enter your Anthropic/OpenAI/Gemini API key")
        print("  4. Optionally delete the demo profile from Settings -> Danger Zone")
        return profile_id


if __name__ == "__main__":
    try:
        seed()
    except Exception as exc:
        print(f"\u2717 Seed failed: {exc}", file=sys.stderr)
        sys.exit(1)
