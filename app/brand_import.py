"""Parse brand brief packs into business create payloads."""

from __future__ import annotations

import re
from typing import Any


_DRIVE_CONTEXT_PATTERN = re.compile(
    r"(?ms)^\n?<!-- google-drive-import:start -->.*?<!-- google-drive-import:end -->\n?"
)


def parse_brand_markdown(text: str) -> list[dict[str, str]]:
    """
    Split a multi-brand brief on markdown headings (# or ##).
    Each heading becomes a business name; following body → context_md.
    """
    text = (text or "").strip()
    if not text:
        return []

    # Explicit separator blocks: ---BRAND: Name---
    if re.search(r"(?im)^---\s*brand\s*:\s*.+---\s*$", text):
        parts = re.split(r"(?im)^---\s*brand\s*:\s*(.+?)\s*---\s*$", text)
        out: list[dict[str, str]] = []
        # parts[0] preamble; then name, body, name, body...
        i = 1
        while i + 1 < len(parts):
            name = parts[i].strip()
            body = parts[i + 1].strip()
            if name:
                out.append(
                    {
                        "name": name,
                        "description": _first_line(body),
                        "context_md": body,
                    }
                )
            i += 2
        return out

    matches = list(re.finditer(r"(?m)^(#{1,3})\s+(.+?)\s*$", text))
    if not matches:
        # Single brand dump — require a title line
        first = text.splitlines()[0].strip() if text else "Imported brand"
        name = first.lstrip("#").strip() or "Imported brand"
        return [{"name": name[:80], "description": "", "context_md": text}]

    out = []
    for idx, m in enumerate(matches):
        name = m.group(2).strip()
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        # Skip tiny TOC-only headings with no body if next heading is immediate
        if not name:
            continue
        out.append(
            {
                "name": name[:120],
                "description": _first_line(body),
                "context_md": f"# {name}\n\n{body}".strip(),
            }
        )
    return out


def _first_line(body: str) -> str:
    for line in (body or "").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return s[:200]
    return ""


def normalize_import_items(items: list[dict[str, Any]] | None, markdown: str | None) -> list[dict[str, str]]:
    if items:
        cleaned = []
        for it in items:
            name = str(it.get("name") or "").strip()
            if not name:
                continue
            cleaned.append(
                {
                    "name": name[:120],
                    "description": str(it.get("description") or "")[:300],
                    "context_md": str(it.get("context_md") or it.get("brief") or ""),
                    "status": str(it.get("status") or "active"),
                }
            )
        return cleaned
    return parse_brand_markdown(markdown or "")


def merge_drive_context(context_md: str, files: list[dict[str, Any]]) -> str:
    """Replace the managed Drive section while preserving manually maintained context."""
    existing = _DRIVE_CONTEXT_PATTERN.sub("", context_md or "").strip()
    if not files:
        return existing

    sections = ["## Google Drive source material"]
    for file in files:
        sections.append(f"### {file.get('name') or '(untitled)'}")
        if file.get("id"):
            sections.append(f"Drive file ID: {file['id']}")
        if file.get("mime_type"):
            sections.append(f"File type: {file['mime_type']}")
        if file.get("web_view_link"):
            sections.append(f"[Open in Google Drive]({file['web_view_link']})")
        if file.get("modified_time"):
            sections.append(f"Last modified: {file['modified_time']}")

    drive_section = (
        "<!-- google-drive-import:start -->\n"
        + "\n\n".join(sections)
        + "\n<!-- google-drive-import:end -->"
    )
    return f"{existing}\n\n{drive_section}".strip() if existing else drive_section
