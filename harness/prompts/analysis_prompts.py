"""Prompt text for the analyze step. Each skill has ONLY a primary
block — when a skill is not selected, it is not mentioned in the
prompt at all. Analysis stays strictly scoped to what the user picked."""

from __future__ import annotations

SKILLS = {
    "security": {
        "label": "Security Analysis",
        "primary": (
            "SECURITY ANALYSIS: Perform a deep security audit. "
            "Check dependencies for known CVEs (composer audit / npm audit or "
            "equivalent), injection risks (SQL, command, LDAP, XXE), auth/session "
            "handling, hardcoded secrets, and CSRF/XSS protections. For each "
            "finding, give exact file/line references where possible."
        ),
    },
    "backward_compat": {
        "label": "Backward Compatibility",
        "primary": (
            "BACKWARD COMPATIBILITY: Identify anything that "
            "would break existing integrations, API contracts, database "
            "schemas, config file formats, or external consumers of this app "
            "if it were changed or migrated. Flag breaking vs. non-breaking "
            "changes explicitly."
        ),
    },
    "architecture": {
        "label": "Architecture Analysis",
        "primary": (
            "ARCHITECTURE ANALYSIS: Document the app's module "
            "boundaries, layering (e.g. MVC), key design patterns used, "
            "dependency direction between modules, and structural tech debt. "
            "Call out anything that would make migration to a new stack "
            "harder (tight coupling, god classes, circular dependencies)."
        ),
    },
    "performance": {
        "label": "Performance Analysis",
        "primary": (
            "PERFORMANCE ANALYSIS: Identify likely performance "
            "bottlenecks — N+1 queries, missing indexes, unbounded loops over "
            "DB results, synchronous calls that should be async/queued, "
            "large payloads without pagination."
        ),
    },
    "code_quality": {
        "label": "Code Quality & Maintainability",
        "primary": (
            "CODE QUALITY: Assess maintainability — dead code, "
            "duplicated logic, missing/inadequate tests, inconsistent style, "
            "overly complex functions, and static-analysis suppressions "
            "(e.g. baseline files) that mask real issues."
        ),
    },
    "preserve_ui_ux": {
        "label": "Preserve UI/UX Fidelity",
        "primary": (
            "UI/UX FIDELITY RISK REVIEW: Identify anything about this app's "
            "existing forms, list/table views, filters, labels, workflows, "
            "and layout conventions that a future rewrite could easily get "
            "wrong or oversimplify — e.g. list views with many columns, "
            "non-obvious default sort orders, field validation rules, "
            "conditional/dynamic form behavior, or naming that isn't obvious "
            "from the database schema alone. Flag these explicitly as "
            "'UX fidelity risk' items so a later migration pass knows exactly "
            "what to preserve precisely rather than approximate."
        ),
    },
}

OUTPUT_FORMAT_INSTRUCTIONS = (
    "\n\nOUTPUT FORMAT — this is critical:\n"
    "After your analysis, output your findings AND a codebase map, in this "
    "exact order, wrapped exactly like this (nothing else after the final "
    "closing marker):\n\n"
    "<<<JSON_START>>>\n"
    "[\n"
    "  {\n"
    '    "category": "security | backward_compat | architecture | performance | code_quality | preserve_ui_ux",\n'
    '    "severity": "critical | high | medium | low | info",\n'
    '    "title": "Short one-line title",\n'
    '    "summary": "One plain-language sentence a non-technical reader can understand - what this means for the app, not how it works internally.",\n'
    '    "description": "1-3 sentence technical explanation of the issue.",\n'
    '    "location": "file path / line number if applicable, else empty string",\n'
    '    "recommendation": "1-2 sentence concrete fix or next step"\n'
    "  }\n"
    "]\n"
    "<<<JSON_END>>>\n\n"
    "<<<CODEBASE_MAP_START>>>\n"
    "A concise structural map of the codebase you just explored, for reuse "
    "by a LATER migration step so it does not need to re-read the repo "
    "from scratch. Include:\n"
    "- Main modules/feature areas and their directory locations\n"
    "- Key controller/route files and what each handles\n"
    "- Main form templates and list/table view templates, with file paths\n"
    "- Database schema location and how to read it (e.g. install/mysql/*.sql)\n"
    "- Any naming/folder conventions worth knowing\n"
    "Write this as plain markdown, concise but specific with real file "
    "paths - a later Claude call should be able to jump straight to the "
    "right files using only this map, without re-exploring the repo.\n"
    "<<<CODEBASE_MAP_END>>>\n\n"
    "The 'summary' field is shown to non-technical readers FIRST, before "
    "any technical detail - it must stand alone and make sense without "
    "reading 'description'. Use plain ASCII text only in the JSON values - "
    "no smart quotes, no em-dashes, no special unicode punctuation. Use a "
    "plain hyphen (-) instead of em-dash, and straight quotes only."
)


def build_analysis_prompt(repo_path: str, selected_skill_ids: list[str]) -> str:
    """Builds a prompt scoped ONLY to the selected skills. A skill that
    isn't selected is not mentioned at all - no secondary/lighter pass,
    so findings never bleed outside what the user asked for."""
    if not selected_skill_ids:
        selected_skill_ids = ["security"]  # sane default

    blocks = [SKILLS[s]["primary"] for s in selected_skill_ids if s in SKILLS]
    focus_areas = ", ".join(SKILLS[s]["label"] for s in selected_skill_ids if s in SKILLS)

    prompt = (
        f"You are analyzing the codebase at {repo_path} for a legacy "
        f"modernization migration. Your ONLY focus areas are: {focus_areas}. "
        "Do NOT report findings outside these areas, even if you notice "
        "something else - stay strictly within scope.\n\n"
        + "\n\n".join(blocks)
        + OUTPUT_FORMAT_INSTRUCTIONS
    )
    return prompt