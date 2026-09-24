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
            "description": cli_stdout[:1500],
            "location": "", "recommendation": "",
        }]
    try:
        return json.loads(match.group(1).strip())
    except json.JSONDecodeError:
        return [{
            "category": "general", "severity": "info",
            "title": "Malformed JSON output",
            "description": match.group(1)[:1500],
            "location": "", "recommendation": "",
        }]


def generate_pdf_report(pdf_path, repo_url: str, findings: list[dict], skills_used: list[str]):
    from fpdf import FPDF
    from harness.skills import SKILLS

    SEVERITY_COLORS = {
        "critical": (200, 30, 30), "high": (220, 100, 20),
        "medium": (210, 170, 0), "low": (60, 130, 60), "info": (100, 100, 100),
    }

    pdf = FPDF(format="A4")
    pdf.set_margins(left=15, top=15, right=15)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    epw = pdf.w - pdf.l_margin - pdf.r_margin

    def safe_text(s: str) -> str:
        return (s or "").encode("latin-1", errors="replace").decode("latin-1")

    def mc(w, h, text):
        """multi_cell wrapper that always forces X back to the left
        margin first — this environment's fpdf2 does not reliably reset
        X after multi_cell on its own."""
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(w, h, text)
        pdf.set_x(pdf.l_margin)

    pdf.set_font("Helvetica", "B", 16)
    mc(epw, 10, safe_text("Security & Modernization Analysis Report"))
    pdf.set_font("Helvetica", "", 10)
    mc(epw, 6, safe_text(f"Repository: {repo_url}"))
    mc(epw, 6, safe_text(f"Skills applied: {', '.join(skills_used)}"))
    pdf.ln(4)

    counts = {}
    for f in findings:
        sev = f.get("severity", "info")
        counts[sev] = counts.get(sev, 0) + 1
    summary_line = "  |  ".join(f"{counts[s]} {s.title()}" for s in ["critical","high","medium","low","info"] if s in counts)
    if summary_line:
        pdf.set_font("Helvetica", "B", 11)
        mc(epw, 6, safe_text(f"Summary: {summary_line}"))
        pdf.ln(3)

    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    grouped = {}
    for f in findings:
        grouped.setdefault(f.get("category", "general"), []).append(f)

    ordered_categories = [s for s in skills_used if s in grouped] + [c for c in grouped if c not in skills_used]

    for cat in ordered_categories:
        cat_label = SKILLS.get(cat, {}).get("label", cat.replace("_", " ").title())

        if pdf.get_y() > pdf.page_break_trigger - 20:
            pdf.add_page()

        pdf.set_font("Helvetica", "B", 13)
        mc(epw, 8, safe_text(cat_label))

        for f in sorted(grouped[cat], key=lambda x: order.get(x.get("severity", "info"), 4)):
            estimated_height = 7 + 5 + 5 + 5 + 8
            if pdf.get_y() + estimated_height > pdf.page_break_trigger:
                pdf.add_page()

            color = SEVERITY_COLORS.get(f.get("severity", "info"), (0, 0, 0))
            sev_label = f.get("severity", "info").upper()
            title_text = f.get("title", "")

            marker_y = pdf.get_y()
            pdf.set_fill_color(*color)
            pdf.rect(pdf.l_margin, marker_y + 1, 4, 4, style="F")

            pdf.set_xy(pdf.l_margin + 7, marker_y)
            pdf.set_font("Helvetica", "B", 11)
            pdf.multi_cell(epw - 7, 7, safe_text(f"[{sev_label}] {title_text}"))
            pdf.set_x(pdf.l_margin)

            pdf.set_font("Helvetica", "", 10)
            if f.get("location"):
                mc(epw, 5, safe_text(f"Location: {f.get('location')}"))
            mc(epw, 5, safe_text(f.get("description", "")))
            if f.get("recommendation"):
                pdf.set_font("Helvetica", "I", 10)
                mc(epw, 5, safe_text(f"Recommendation: {f.get('recommendation')}"))
            pdf.ln(3)
        pdf.ln(4)

    Path(pdf_path).parent.mkdir(exist_ok=True)
    pdf.output(str(pdf_path))