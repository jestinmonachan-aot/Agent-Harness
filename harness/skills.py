"""Skill definitions for the analyze step. Each skill has a `primary`
prompt block (used when the user selects it — deep focus) and a
`secondary` block (a lighter pass — used when NOT selected, so nothing
is ever fully ignored)."""

from __future__ import annotations

SKILLS = {
    "security": {
        "label": "Security Analysis",
        "primary": (
            "SECURITY ANALYSIS (primary focus): Perform a deep security audit. "
            "Check dependencies for known CVEs (composer audit / npm audit or "
            "equivalent), injection risks (SQL, command, LDAP, XXE), auth/session "
            "handling, hardcoded secrets, and CSRF/XSS protections. For each "
            "finding, give exact file/line references where possible."
        ),
        "secondary": (
            "Security (light check): note any obviously critical security "
            "red flags you notice in passing (hardcoded secrets, disabled "
            "auth, obvious injection), but do not do a deep audit."
        ),
    },
    "backward_compat": {
        "label": "Backward Compatibility",
        "primary": (
            "BACKWARD COMPATIBILITY (primary focus): Identify anything that "
            "would break existing integrations, API contracts, database "
            "schemas, config file formats, or external consumers of this app "
            "if it were changed or migrated. Flag breaking vs. non-breaking "
            "changes explicitly."
        ),
        "secondary": (
            "Backward compatibility (light check): note any obvious breaking-"
            "change risks you notice, without a full compatibility audit."
        ),
    },
    "architecture": {
        "label": "Architecture Analysis",
        "primary": (
            "ARCHITECTURE ANALYSIS (primary focus): Document the app's module "
            "boundaries, layering (e.g. MVC), key design patterns used, "
            "dependency direction between modules, and structural tech debt. "
            "Call out anything that would make migration to a new stack "
            "harder (tight coupling, god classes, circular dependencies)."
        ),
        "secondary": (
            "Architecture (light check): briefly note the overall structure "
            "and any obvious major red flags, without a full deep-dive."
        ),
    },
    "performance": {
        "label": "Performance Analysis",
        "primary": (
            "PERFORMANCE ANALYSIS (primary focus): Identify likely performance "
            "bottlenecks — N+1 queries, missing indexes, unbounded loops over "
            "DB results, synchronous calls that should be async/queued, "
            "large payloads without pagination."
        ),
        "secondary": (
            "Performance (light check): note any obvious performance red "
            "flags you notice, without a deep profiling pass."
        ),
    },
    "code_quality": {
        "label": "Code Quality & Maintainability",
        "primary": (
            "CODE QUALITY (primary focus): Assess maintainability — dead code, "
            "duplicated logic, missing/inadequate tests, inconsistent style, "
            "overly complex functions, and static-analysis suppressions "
            "(e.g. baseline files) that mask real issues."
        ),
        "secondary": (
            "Code quality (light check): note any glaring maintainability "
            "issues, without a full quality audit."
        ),
    },
}

OUTPUT_FORMAT_INSTRUCTIONS = (
    "\n\nOUTPUT FORMAT — this is critical:\n"
    "After your analysis, output a SINGLE JSON array as your final output, "
    "wrapped exactly like this (nothing else after the closing marker):\n\n"
    "<<<JSON_START>>>\n"
    "[\n"
    "  {\n"
    '    "category": "security | backward_compat | architecture | performance | code_quality",\n'
    '    "severity": "critical | high | medium | low | info",\n'
    '    "title": "Short one-line title",\n'
    '    "description": "1-3 sentence plain-language explanation of the issue.",\n'
    '    "location": "file path / line number if applicable, else empty string",\n'
    '    "recommendation": "1-2 sentence concrete fix or next step"\n'
    "  }\n"
    "]\n"
    "<<<JSON_END>>>\n\n"
    "Use plain ASCII text only in the JSON values — no smart quotes, no "
    "em-dashes, no special unicode punctuation. Use a plain hyphen (-) "
    "instead of em-dash, and straight quotes only."
)


def build_analysis_prompt(repo_path: str, selected_skill_ids: list[str]) -> str:
    if not selected_skill_ids:
        selected_skill_ids = ["security"]  # sane default

    blocks = []
    for skill_id, skill in SKILLS.items():
        if skill_id in selected_skill_ids:
            blocks.append(skill["primary"])
        else:
            blocks.append(skill["secondary"])

    focus_areas = ", ".join(SKILLS[s]["label"] for s in selected_skill_ids)

    prompt = (
        f"You are analyzing the codebase at {repo_path} for a legacy "
        f"modernization migration. Your MAIN focus areas are: {focus_areas}. "
        "You should also briefly touch on the other areas listed below "
        "(lighter pass), so nothing is completely ignored.\n\n"
        + "\n\n".join(blocks)
        + OUTPUT_FORMAT_INSTRUCTIONS
    )
    return prompt