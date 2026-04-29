"""LLM prompt templates."""

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import settings

_env = Environment(
    loader=FileSystemLoader(str(settings.prompts_dir)),
    autoescape=select_autoescape([]),  # Prompts are not HTML
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_prompt(template_name: str, **context: Any) -> str:
    """Render a Jinja2 prompt template."""
    template = _env.get_template(template_name)
    return template.render(**context)
