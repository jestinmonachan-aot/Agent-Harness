import json
import re
from pathlib import Path

def extract_json_findings(cli_stdout: str) -> list[dict]:
    """Pull the JSON array out between markers. Falls back to a single
    'raw' finding if parsing fails, so nothing is silently lost."""
    match = re.search(r"<<<JSON_START>>>(.*?)<<<JSON_END>>>", cli_stdout, re.DOTALL)
    if not match:
        return [{
            "category": "general", "severity": "info",
            "title": "Unstructured output (parsing failed)",
            "summary": "The analysis produced output that could not be parsed automatically.",
            "description": cli_stdout[:1500],
            "location": "", "recommendation": "",
        }]
    try:
        return json.loads(match.group(1).strip())
    except json.JSONDecodeError:
        return [{
            "category": "general", "severity": "info",
            "title": "Malformed JSON output",
            "summary": "The analysis produced output that could not be parsed automatically.",
            "description": match.group(1)[:1500],
            "location": "", "recommendation": "",
        }]
    
def extract_codebase_map(cli_stdout: str) -> str:
    """Pull the codebase map markdown out between markers. Empty string
    if not found, so callers can fall back to the old re-explore behavior."""
    match = re.search(r"<<<CODEBASE_MAP_START>>>(.*?)<<<CODEBASE_MAP_END>>>", cli_stdout, re.DOTALL)
    return match.group(1).strip() if match else ""



def extract_module_names(codebase_map: str) -> list[str]:
    """Pulls module/feature-area names out of ONLY the codebase_map's
    'Main modules/feature areas' section (bounded by the next heading or
    end of text) - not the whole document. Best-effort - falls back to
    an empty list if no such section is found, and the caller should
    always keep 'Full app' / manual entry as a fallback option."""

    lines = codebase_map.splitlines()

    # Find the start of the "main modules" section - a line that looks
    # like a heading (markdown # or bold, or a short standalone label)
    # and mentions "module" or "feature area".
    start_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip().strip("#*_ ").lower()
        if "module" in stripped and len(stripped) < 60:
            start_idx = i + 1
            break
        if "feature area" in stripped and len(stripped) < 60:
            start_idx = i + 1
            break

    if start_idx is None:
        return []

    # Collect bullet lines until the next heading-like line (markdown
    # heading, bold-only line, or a short line with no bullet marker
    # that looks like a new section title) or end of text.
    names = []
    for line in lines[start_idx:]:
        raw = line.rstrip()
        stripped = raw.strip()

        if not stripped:
            continue  # blank lines don't end the section by themselves

        is_bullet = stripped.startswith(("-", "*")) and not stripped.startswith(("**",))
        looks_like_heading = (
            stripped.startswith("#")
            or (not is_bullet and len(stripped) < 60 and not stripped[0].isdigit()
                and ":" not in stripped and "/" not in stripped and "`" not in stripped)
        )

        if not is_bullet and looks_like_heading:
            break  # hit the next section

        if not is_bullet:
            continue  # stray prose line inside the section, skip it

        text = stripped.lstrip("-* ").strip().strip("*_")
        # Cut at the first delimiter that separates a name from its detail
        for delim in [":", " - ", " (", "(", "`"]:
            if delim in text:
                text = text.split(delim, 1)[0].strip()
                break
        text = text.strip("*_ `")

        # Reject anything that still looks like a file path or code token
        if not text or "/" in text or "." in text or "`" in text:
            continue
        if not (2 <= len(text) <= 40):
            continue
        if text.lower() in ("module", "modules", "feature area", "feature areas"):
            continue

        names.append(text)

    # De-dupe, preserve order
    seen = set()
    result = []
    for n in names:
        key = n.lower()
        if key not in seen:
            seen.add(key)
            result.append(n)
    return result[:15]


    return result[:15]  # sane cap

def generate_pdf_report(pdf_path, repo_url: str, findings: list[dict], skills_used: list[str]):
    from fpdf import FPDF
    from harness.skills import SKILLS

    SEVERITY_COLORS = {
        "critical": (200, 30, 30), "high": (220, 100, 20),
        "medium": (200, 160, 0), "low": (60, 130, 60), "info": (110, 110, 110),
    }
    MUTED_TEXT = (100, 100, 100)
    HEADING_COLOR = (30, 30, 30)

    pdf = FPDF(format="A4")
    pdf.set_margins(left=18, top=18, right=18)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    epw = pdf.w - pdf.l_margin - pdf.r_margin

    def safe_text(s: str) -> str:
        return (s or "").encode("latin-1", errors="replace").decode("latin-1")

    def mc(w, h, text, align="L"):
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(w, h, text, align=align)
        pdf.set_x(pdf.l_margin)

    # ---- Cover / header ----
    pdf.set_text_color(*HEADING_COLOR)
    pdf.set_font("Helvetica", "B", 18)
    mc(epw, 10, safe_text("Modernization Analysis Report"))

    pdf.set_text_color(*MUTED_TEXT)
    pdf.set_font("Helvetica", "", 10)
    mc(epw, 6, safe_text(f"Repository: {repo_url}"))
    focus_labels = ", ".join(SKILLS.get(s, {}).get("label", s) for s in skills_used)
    mc(epw, 6, safe_text(f"Focus areas: {focus_labels}"))
    pdf.set_text_color(0, 0, 0)
    pdf.ln(5)

    # ---- Summary line ----
    counts = {}
    for f in findings:
        sev = f.get("severity", "info")
        counts[sev] = counts.get(sev, 0) + 1
    if counts:
        pdf.set_font("Helvetica", "B", 12)
        mc(epw, 7, safe_text("Summary"))
        pdf.set_font("Helvetica", "", 10)
        x = pdf.l_margin
        y = pdf.get_y()
        for sev in ["critical", "high", "medium", "low", "info"]:
            if sev not in counts:
                continue
            color = SEVERITY_COLORS.get(sev, (0, 0, 0))
            label = f"{counts[sev]} {sev.title()}"
            pdf.set_xy(x, y)
            pdf.set_fill_color(*color)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font("Helvetica", "B", 9)
            w = pdf.get_string_width(label) + 6
            pdf.cell(w, 7, safe_text(label), fill=True, align="C")
            pdf.set_text_color(0, 0, 0)
            x += w + 4
        pdf.ln(12)
    else:
        pdf.set_font("Helvetica", "", 11)
        mc(epw, 7, safe_text("No issues found."))
        pdf.ln(4)

    # ---- Findings, grouped by focus area ----
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    grouped = {}
    for f in findings:
        grouped.setdefault(f.get("category", "general"), []).append(f)

    ordered_categories = [s for s in skills_used if s in grouped] + [c for c in grouped if c not in skills_used]

    for cat in ordered_categories:
        cat_label = SKILLS.get(cat, {}).get("label", cat.replace("_", " ").title())

        if pdf.get_y() > pdf.page_break_trigger - 25:
            pdf.add_page()

        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(*HEADING_COLOR)
        mc(epw, 9, safe_text(cat_label))
        pdf.set_draw_color(200, 200, 200)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.set_text_color(0, 0, 0)
        pdf.ln(4)

        for f in sorted(grouped[cat], key=lambda x: order.get(x.get("severity", "info"), 4)):
            if pdf.get_y() > pdf.page_break_trigger - 30:
                pdf.add_page()

            color = SEVERITY_COLORS.get(f.get("severity", "info"), (0, 0, 0))
            sev_label = f.get("severity", "info").upper()
            title_text = f.get("title", "")
            summary_text = f.get("summary") or f.get("description", "")

            # Severity marker + title (plain-language first)
            marker_y = pdf.get_y()
            pdf.set_fill_color(*color)
            pdf.rect(pdf.l_margin, marker_y + 1.5, 4, 4, style="F")

            pdf.set_xy(pdf.l_margin + 7, marker_y)
            pdf.set_font("Helvetica", "B", 11)
            pdf.multi_cell(epw - 7, 6.5, safe_text(f"{title_text}  ({sev_label})"))
            pdf.set_x(pdf.l_margin)

            # Plain-language summary — the primary, always-visible content
            pdf.set_font("Helvetica", "", 10.5)
            mc(epw, 5.5, safe_text(summary_text))
            pdf.ln(1)

            # Technical details — visually secondary, clearly labeled
            has_technical = f.get("location") or f.get("description")
            if has_technical:
                pdf.set_font("Helvetica", "BI", 8.5)
                pdf.set_text_color(*MUTED_TEXT)
                mc(epw, 5, safe_text("Technical details"))
                pdf.set_font("Helvetica", "", 9)
                if f.get("location"):
                    mc(epw, 4.8, safe_text(f"Location: {f.get('location')}"))
                if f.get("description") and f.get("description") != summary_text:
                    mc(epw, 4.8, safe_text(f.get("description", "")))
                pdf.set_text_color(0, 0, 0)

            if f.get("recommendation"):
                pdf.set_font("Helvetica", "I", 9.5)
                mc(epw, 5, safe_text(f"Recommendation: {f.get('recommendation')}"))

            pdf.ln(4)
        pdf.ln(3)

    Path(pdf_path).parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(pdf_path))