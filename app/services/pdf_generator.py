"""Generate PDFs from HTML using Playwright."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import settings

logger = logging.getLogger(__name__)

_template_env = Environment(
    loader=FileSystemLoader(str(settings.templates_dir)),
    autoescape=select_autoescape(["html"]),
)


PAPER_SIZES = {
    "letter": ("8.5in", "11in"),
    "a4": ("210mm", "297mm"),
}


def render_resume_html(
    template_data: dict,
    paper_size: str = "letter",
    lang: str = "en",
) -> str:
    """Render the CV template with the given content."""
    template = _template_env.get_template("cv-template.html")
    page_width, page_height = PAPER_SIZES.get(paper_size.lower(), PAPER_SIZES["letter"])
    return template.render(
        **template_data,
        page_width=page_width,
        page_height=page_height,
        lang=lang,
    )


async def html_to_pdf(html: str, output_path: Path, paper_format: str = "letter") -> Path:
    """Convert HTML string to PDF using Playwright."""
    from playwright.async_api import async_playwright

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write HTML to temp file so Playwright can navigate to it
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8"
    ) as f:
        f.write(html)
        temp_html = Path(f.name)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(temp_html.resolve().as_uri(), wait_until="networkidle")
            await page.pdf(
                path=str(output_path),
                format="Letter" if paper_format.lower() == "letter" else "A4",
                print_background=True,
                margin={"top": "0.6in", "bottom": "0.6in", "left": "0.6in", "right": "0.6in"},
            )
            await browser.close()
    finally:
        try:
            temp_html.unlink()
        except Exception:
            pass

    return output_path


async def generate_resume_pdf(
    template_data: dict,
    output_path: Path,
    paper_size: str = "letter",
    lang: str = "en",
) -> Path:
    """One-shot: render template to HTML, then convert to PDF."""
    html = render_resume_html(template_data, paper_size=paper_size, lang=lang)
    return await html_to_pdf(html, output_path, paper_format=paper_size)
