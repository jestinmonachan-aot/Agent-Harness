"""
Utilities to read/write .md files (e.g. CLAUDE.md, analysis reports)
so we can prove the harness can manipulate markdown programmatically.
"""

from __future__ import annotations

from pathlib import Path


def read_md(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def write_md(path: str | Path, content: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def append_section(path: str | Path, heading: str, body: str) -> None:
    """Append a new `## heading` section with body text to an existing
    .md file, creating the file if it doesn't exist yet."""
    p = Path(path)
    existing = p.read_text(encoding="utf-8") if p.exists() else ""
    section = f"\n\n## {heading}\n\n{body}\n"
    write_md(p, existing + section)


def save_analysis_report(path: str | Path, repo_url: str, findings: str) -> None:
    """Write a structured vulnerability-analysis report as markdown."""
    content = (
        f"# Vulnerability Analysis Report\n\n"
        f"**Repo:** {repo_url}\n\n"
        f"## Findings\n\n{findings}\n"
    )
    write_md(path, content)
