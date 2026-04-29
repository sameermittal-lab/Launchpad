"""Parse structured resume markdown into the template data the PDF uses.

Works for both the user's base cv.md AND tailored resume markdown,
as long as they follow the same section structure.
"""

from __future__ import annotations

import re
from typing import Optional


def _split_sections(md: str) -> dict[str, str]:
    """Split markdown by top-level ## headings. Returns {heading_lower: content}."""
    sections: dict[str, str] = {}
    current_title: Optional[str] = None
    current_lines: list[str] = []
    for line in md.splitlines():
        # Match ## Heading but NOT ### (which is inside Work Experience)
        m = re.match(r"^##\s+(.+)$", line)
        if m and not line.startswith("### "):
            if current_title is not None:
                sections[current_title.lower()] = "\n".join(current_lines).strip()
            current_title = m.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_title is not None:
        sections[current_title.lower()] = "\n".join(current_lines).strip()
    return sections


def _extract_header(md: str) -> dict:
    """Extract name + contact line from the top of the markdown."""
    data: dict = {
        "name": "",
        "email": None,
        "phone": None,
        "location": None,
        "linkedin": None,
        "portfolio": None,
    }
    lines = md.splitlines()
    for line in lines[:20]:
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^#\s+(.+)$", line)
        if m:
            data["name"] = m.group(1).strip()
            continue
        if line.startswith("##") or line.startswith("-"):
            break
        # Parse contact line with · or | as separators
        parts = re.split(r"\s*[·\|]\s*", line)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            # Tolerate "Email:", "Phone:" prefixes
            part_clean = re.sub(r"^(Email|Phone|Location|LinkedIn|Portfolio):\s*", "", part, flags=re.I)
            if "@" in part_clean and "." in part_clean and not data["email"]:
                data["email"] = part_clean
            elif re.search(r"\+?\d[\d\s\-()]{6,}", part_clean) and not data["phone"]:
                data["phone"] = part_clean
            elif "linkedin.com" in part_clean.lower() and not data["linkedin"]:
                data["linkedin"] = part_clean
            elif re.match(r"https?://", part_clean) and not data["portfolio"]:
                data["portfolio"] = part_clean
            elif not data["location"]:
                data["location"] = part_clean
    return data


def _parse_bullet_list(text: str) -> list[str]:
    """Extract '- item' bullets from text."""
    items = []
    for line in text.splitlines():
        m = re.match(r"^\s*[-*]\s+(.+)$", line)
        if m:
            items.append(m.group(1).strip())
    return items


def _parse_experience(text: str) -> list[dict]:
    """Parse Work Experience section: ### Company - Role \\n **dates** \\n - bullets."""
    jobs: list[dict] = []
    current: Optional[dict] = None
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        # ### Company - Role
        m = re.match(r"^###\s+(.+)$", line)
        if m:
            if current:
                jobs.append(current)
            header = m.group(1).strip()
            # Try "Company - Role" split
            if " - " in header:
                parts = header.split(" - ", 1)
                company = parts[0].strip()
                role = parts[1].strip()
            elif " — " in header:
                parts = header.split(" — ", 1)
                company = parts[0].strip()
                role = parts[1].strip()
            elif "," in header:
                parts = header.split(",", 1)
                company = parts[1].strip()
                role = parts[0].strip()
            else:
                company = header
                role = ""
            current = {"company": company, "title": role, "date_range": "", "bullets": []}
            i += 1
            # Look for a **dates** line right after
            while i < len(lines) and not lines[i].strip():
                i += 1
            if i < len(lines):
                date_m = re.match(r"^\*\*(.+?)\*\*\s*$", lines[i].strip())
                if date_m:
                    current["date_range"] = date_m.group(1).strip()
                    i += 1
            continue
        # Bullet
        bm = re.match(r"^\s*[-*]\s+(.+)$", line)
        if bm and current:
            current["bullets"].append(bm.group(1).strip())
        i += 1
    if current:
        jobs.append(current)
    return jobs


def _parse_projects(text: str) -> list[dict]:
    """### Project Name \\n description."""
    out: list[dict] = []
    current: Optional[dict] = None
    for line in text.splitlines():
        m = re.match(r"^###\s+(.+)$", line)
        if m:
            if current:
                out.append(current)
            current = {"name": m.group(1).strip(), "description": ""}
            continue
        if current and line.strip():
            current["description"] = (current["description"] + " " + line.strip()).strip()
    if current:
        out.append(current)
    return out


def _parse_education(text: str) -> list[dict]:
    """### Degree, Institution \\n year."""
    out: list[dict] = []
    current: Optional[dict] = None
    for line in text.splitlines():
        m = re.match(r"^###\s+(.+)$", line)
        if m:
            if current:
                out.append(current)
            header = m.group(1).strip()
            if "," in header:
                degree, school = header.split(",", 1)
                current = {"degree": degree.strip(), "school": school.strip(), "year": ""}
            else:
                current = {"degree": header, "school": "", "year": ""}
            continue
        if current and line.strip() and not current.get("year"):
            current["year"] = line.strip()
    if current:
        out.append(current)
    return out


def _parse_skills(text: str) -> dict:
    """**Technical:** ... **Languages:** ..."""
    technical = ""
    languages = ""
    for line in text.splitlines():
        tm = re.match(r"^\*\*Technical:?\*\*\s*(.+)$", line.strip(), re.I)
        if tm:
            technical = tm.group(1).strip()
        lm = re.match(r"^\*\*Languages?:?\*\*\s*(.+)$", line.strip(), re.I)
        if lm:
            languages = lm.group(1).strip()
    return {"skills_technical": technical, "skills_languages": languages}


def parse_resume_md(md: str) -> dict:
    """Parse resume markdown into template data dict for cv-template.html.

    Expected structure:
      # Name
      email · phone · location · linkedin
      ## Professional Summary ...
      ## Core Competencies ...
      ## Work Experience ...
      ## Projects ...
      ## Education ...
      ## Certifications ...
      ## Skills ...
    """
    header = _extract_header(md)
    sections = _split_sections(md)

    def first_section(*keys):
        for k in keys:
            if k in sections and sections[k]:
                return sections[k]
        return ""

    summary = first_section("professional summary", "summary")
    competencies = _parse_bullet_list(first_section("core competencies", "competencies"))
    experience = _parse_experience(first_section("work experience", "experience"))
    projects = _parse_projects(first_section("projects"))
    education = _parse_education(first_section("education"))
    certifications = _parse_bullet_list(first_section("certifications"))
    skills = _parse_skills(first_section("skills"))

    return {
        **header,
        "summary": summary,
        "competencies": competencies,
        "experience": experience,
        "projects": projects,
        "education": education,
        "certifications": certifications,
        **skills,
    }
