"""Prompt text for the migration step. Kept separate from
harness/migrate.py's orchestration logic so wording can be reviewed and
edited on its own.

FAST MODE: full-app migrations previously fit a strict single-call time
budget via fast_mode=True on build_direct_prompt, skipping completeness
self-audit / smoke-test steps. That path is still available for narrow
scope. Full-app migrations now go through the module pipeline instead
(build_planning_prompt -> build_module_prompt x N -> build_assembly_prompt),
trading the 1-hour ceiling for real breadth, with per-module audit/
smoke-test steps built in throughout.

CODEBASE MAP: analyze already explores the repo once and produces a
structural map (see harness/prompts/analysis_prompts.py's
CODEBASE_MAP block). Every prompt below accepts codebase_map and, when
given, instructs Claude to treat it as authoritative for overall layout
rather than re-exploring the directory structure from scratch - this
applies to planning and to every individual module, not just the
narrow-scope direct path, since re-exploring per module was pure waste
once analyze had already done it once.
"""

from __future__ import annotations
from pathlib import Path

KNOWLEDGE_BASE_FILENAME = "KNOWLEDGE_BASE.md"


def _codebase_map_block(codebase_map: str, scope_hint: str) -> str:
    if not codebase_map:
        return ""
    return (
        f"A prior analysis pass already explored this codebase and produced "
        f"the structural map below. Treat it as authoritative for overall "
        f"layout - do NOT re-explore the directory structure from scratch. "
        f"Use it to jump straight to the specific files you need for "
        f"{scope_hint}. Still actually open and read those specific files "
        f"for exact field names/validation/etc - the map tells you WHERE to "
        f"look, not what's inside each file.\n\n"
        f"--- CODEBASE MAP (from prior analysis) ---\n{codebase_map}\n"
        f"--- END CODEBASE MAP ---\n\n"
    )


def build_direct_prompt(
    input_repo_path: str, findings_md: str, scope: str, fast_mode: bool = False,
    codebase_map: str = "",
) -> str:
    audit_steps = (
        "7. As the LAST LINE of your final response, output exactly:\n"
        "STACK_CHOSEN: <short stack name>\n"
    ) if fast_mode else (
        "7. BEFORE writing your final summary, do a completeness self-audit: "
        "go back through EXISTING_UX_INVENTORY.md line by line and confirm "
        "each field/column/flow is actually implemented and reachable in the "
        "UI you just built - not just modeled in the backend. If anything is "
        "missing, go back and implement it now; do not report a gap as a "
        "'follow-up' or 'known limitation' unless it is genuinely absent from "
        "the legacy app too. Write PARITY_CHECK.md listing each inventory "
        "item and a one-word DONE/MISSING status - if anything is MISSING, "
        "fix it before finishing.\n"
        "8. Before finishing, actually start the app (backend + frontend dev "
        "servers) and smoke-test every implemented flow with a real request "
        "(not just reading your own code) - creating an item, listing it, "
        "editing it, and any flow-specific actions. Fix anything that "
        "errors. Do not claim something works without having run it.\n"
        "9. As the LAST LINE of your final response, output exactly:\n"
        "STACK_CHOSEN: <short stack name>\n"
    )

    codebase_map_block = _codebase_map_block(codebase_map, f"'{scope}'")

    return (
        f"You are migrating ONLY the '{scope}' feature/module of a legacy "
        f"application to a modern tech stack of YOUR CHOOSING. Analyze the "
        f"codebase and pick the best-fit modern stack (e.g. FastAPI+React, "
        f"Django+Vue, Spring Boot+Angular, etc.) based on what the app "
        f"actually needs. This is not optional - choose one and justify it.\n\n"
        f"Legacy source (read-only, do not modify): {input_repo_path}\n\n"
        f"{codebase_map_block}"
        f"Known vulnerabilities/issues to fix during the rewrite:\n{findings_md}\n\n"
        "REQUIREMENTS:\n"
        f"1. FIRST, before writing any new code: search the legacy source for "
        f"every file relevant to '{scope}' - controllers/routes, form "
        "templates, LIST/TABLE VIEW templates, and any partials they include. "
        "A feature area typically has BOTH a form (create/edit single item) "
        "AND a list view (the table shown when browsing many items) - find "
        "and read both; a common failure is inventorying only the form and "
        "shipping a generic table for the list view.\n"
        "Write EXISTING_UX_INVENTORY.md with TWO sections:\n"
        "   a) FORM: every field name, its type/options, validation rules, "
        "default values, and every user-facing flow/action.\n"
        "   b) LIST VIEW: the exact column set and column order shown in the "
        "legacy table/grid, every filter/search control above it (with its "
        "options), default sort order, row-level actions, and pagination "
        "style - as they literally appear in the legacy templates, not a "
        "plausible reconstruction.\n"
        "At the top of EXISTING_UX_INVENTORY.md, list the exact file paths "
        "you read to produce it. Do not skip fields or columns because they "
        "seem minor.\n"
        "2. Write STACK_DECISION.md explaining your stack choice and "
        "reasoning in plain, clear language (a paragraph or two).\n"
        "3. Reimplement this scope from scratch in the chosen stack, using "
        "EXISTING_UX_INVENTORY.md as the field/flow spec - every field, "
        "column, filter, and flow listed there MUST appear in the new "
        "implementation, with the same labels/names, column order, and "
        "options as the legacy app. The list/table view especially must "
        "match the legacy column set - do not substitute a generic or "
        "simplified table. Do not invent, drop, or simplify fields or "
        "columns.\n"
        "4. Fix the listed vulnerabilities as part of the rewrite.\n"
        "5. Do NOT modify anything at the legacy source path.\n"
        "6. Write MIGRATION_NOTES.md summarizing what was migrated.\n"
        + audit_steps
    )


def build_planning_prompt(input_repo_path: str, codebase_map: str = "") -> str:
    codebase_map_block = _codebase_map_block(
        codebase_map, "identifying the app's functional modules"
    )
    return (
        "You are planning a migration of the ENTIRE application to a modern "
        f"stack. Legacy source (read-only): {input_repo_path}\n\n"
        f"{codebase_map_block}"
        "Identify the natural functional modules of the app - the way a user "
        "or admin would describe its distinct feature areas (e.g. 'tickets', "
        "'assets', 'user management', 'authentication', 'reporting'). If the "
        "codebase map above is provided, use it to identify modules directly "
        "rather than re-exploring the directory structure; otherwise, "
        "explore the codebase structure (directory names, controller/route "
        "files, DB schema) enough to identify them yourself. Keep the list "
        "to what's actually distinct: typically 3-8 modules. Keep each "
        "module SMALL enough that it could realistically be read and "
        "reimplemented within about 30-45 minutes of focused work - split a "
        "large area into two modules rather than proposing one oversized "
        "module.\n\n"
        "Order the list so that foundational/shared modules (auth, core "
        "data model, users) come BEFORE modules that depend on them.\n\n"
        "Respond with ONLY a JSON array, nothing else, no markdown fences:\n"
        '[{"id": "auth", "description": "Login, sessions, permissions"}, '
        '{"id": "tickets", "description": "Ticket creation, listing, and '
        'lifecycle"}, ...]\n'
    )


def build_module_prompt(
    input_repo_path: str,
    output_dir: Path,
    module: dict,
    findings_md: str,
    is_first_module: bool,
    codebase_map: str = "",
) -> str:
    module_id = module["id"]
    module_desc = module.get("description", module_id)
    kb_path = output_dir / KNOWLEDGE_BASE_FILENAME
    codebase_map_block = _codebase_map_block(codebase_map, f"the '{module_id}' module")

    if is_first_module:
        knowledge_base_instruction = (
            f"This is the FIRST module being migrated. There is no "
            f"{KNOWLEDGE_BASE_FILENAME} yet - you will create it.\n"
            "Analyze the legacy codebase and pick the best-fit modern stack "
            "(e.g. FastAPI+React, Django+Vue, Spring Boot+Angular, etc.) based "
            "on what the app actually needs. This is not optional - choose "
            "one and justify it in STACK_DECISION.md.\n"
        )
    else:
        knowledge_base_instruction = (
            f"BEFORE reading any legacy source, first read "
            f"{kb_path} - it summarizes the stack, naming/folder conventions, "
            "and shared models/endpoints already established by modules "
            "migrated so far. Reuse those decisions; do not re-derive or "
            "diverge from them. Only consult the legacy source for details "
            "specific to this module.\n"
        )

    return (
        f"You are migrating ONLY the '{module_id}' module ({module_desc}) of a "
        f"legacy application to a modern stack, as part of a larger multi-module "
        f"migration. Other modules may already exist in the output directory - "
        f"do not break them.\n\n"
        f"Legacy source (read-only, do not modify): {input_repo_path}\n"
        f"Output directory (write here, may already contain other modules): {output_dir}\n\n"
        f"{codebase_map_block}"
        f"Known vulnerabilities/issues to fix if relevant to this module:\n{findings_md}\n\n"
        f"{knowledge_base_instruction}\n"
        "REQUIREMENTS:\n"
        f"1. Search the legacy source for every file relevant to the "
        f"'{module_id}' module (use the codebase map above if provided to "
        f"jump straight to the right files; front/, ajax/, src/, templates/, "
        f"install/mysql schema, etc. otherwise) - controllers/routes, FORM "
        "templates, and LIST/TABLE VIEW templates (and any partials they "
        "include). A module typically has BOTH a form (create/edit single "
        "item) AND a list view (the table shown when browsing many items) - "
        "find and read both; a common failure is inventorying only the form "
        f"and shipping a generic table for the list view. Write "
        f"EXISTING_UX_INVENTORY_{module_id}.md with TWO sections:\n"
        "   a) FORM: every field name, its type/options, validation rules, "
        "default values, and every user-facing flow/action.\n"
        "   b) LIST VIEW: the exact column set and column order shown in the "
        "legacy table/grid, every filter/search control above it (with its "
        "options), default sort order, row-level actions, and pagination "
        "style - as they literally appear in the legacy templates, not a "
        "plausible reconstruction.\n"
        "At the top of the file, list the exact file paths you read to "
        "produce it. Do not skip fields or columns because they seem minor - "
        "an incomplete inventory means an incomplete migration.\n"
        f"2. Reimplement the '{module_id}' module using "
        f"EXISTING_UX_INVENTORY_{module_id}.md as the field/flow spec - every "
        "field, column, filter, and flow listed there MUST appear in the new "
        "implementation, with the SAME labels/names, column order, and "
        "options as the legacy app (overall UI/UX should feel identical to "
        "the legacy app, just modernized). The list/table view especially "
        "must match the legacy column set - do not substitute a generic or "
        "simplified table. Do not invent, drop, or simplify fields or "
        "columns. Follow the stack and conventions already established (see "
        "above).\n"
        "3. Fix any of the listed vulnerabilities that fall within this "
        "module's scope.\n"
        "4. Do NOT modify anything at the legacy source path.\n"
        f"5. Append a section to {KNOWLEDGE_BASE_FILENAME} (create it if this "
        "is the first module) covering: the stack chosen (first module only), "
        "naming/folder conventions used, and any shared models/endpoints/"
        "components this module created that later modules should reuse "
        "rather than reinvent. Keep it concise - this is a working reference "
        "for future calls, not documentation.\n"
        f"6. Append a section to MIGRATION_NOTES.md (create it if it doesn't "
        f"exist) summarizing what was migrated for the '{module_id}' module.\n"
        f"7. BEFORE writing your final summary, do a completeness self-audit: "
        f"go back through EXISTING_UX_INVENTORY_{module_id}.md line by line "
        "and confirm each field/column/flow is actually implemented and "
        "reachable in the UI you just built - not just modeled in the "
        "backend. If anything is missing, go back and implement it now; do "
        "not report a gap as a 'follow-up' or 'known limitation' unless it "
        f"is genuinely absent from the legacy app too. Write "
        f"PARITY_CHECK_{module_id}.md listing each inventory item and a "
        "one-word DONE/MISSING status - if anything is MISSING, fix it "
        "before finishing.\n"
        "8. Before finishing, actually start the app (backend + frontend dev "
        "servers, alongside any modules already built) and smoke-test this "
        "module's flows with a real request (not just reading your own "
        "code) - creating an item, listing it, editing it, and any "
        "flow-specific actions. Fix anything that errors. Do not claim "
        "something works without having run it.\n"
        "9. As the LAST LINE of your final response, output exactly:\n"
        "STACK_CHOSEN: <short stack name>\n"
    )


def build_assembly_prompt(output_dir: Path, modules: list[dict], chosen_stack: str) -> str:
    module_list = ", ".join(m["id"] for m in modules)
    return (
        f"You have migrated the following modules of a legacy application into "
        f"{output_dir}, each built individually: {module_list}. The chosen "
        f"stack was {chosen_stack}. {KNOWLEDGE_BASE_FILENAME} in that directory "
        "summarizes decisions made along the way - read it first.\n\n"
        "Now do a final ASSEMBLY pass over the whole output directory:\n"
        "1. Inspect what's actually there across all modules.\n"
        "2. Wire the modules together into one coherent app: shared "
        "navigation/routing so all modules are reachable from a single app "
        "shell, shared layout/auth where appropriate, and resolve any naming "
        "or dependency conflicts between modules.\n"
        "3. Ensure the project builds/runs as a single app (single "
        "package.json/requirements, single entrypoint, consistent config) "
        "rather than N disconnected mini-apps.\n"
        f"4. Cross-check the assembled app against every "
        f"EXISTING_UX_INVENTORY_*.md file in {output_dir} - confirm every "
        "listed field/flow/label is actually present, reachable through the "
        "UI, and functional after wiring - not just present in isolation. "
        "Actually run the assembled app (backend + frontend) and click "
        "through every module's main flows post-integration; integration "
        "frequently breaks things that worked standalone (routing "
        "collisions, shared auth, naming conflicts). Fix anything broken by "
        "assembly before finishing.\n"
        "5. Write a top-level README.md describing the app, its modules, and "
        "how to run it.\n"
        "6. Do not remove or regress functionality already migrated per "
        "module - only integrate and fix conflicts.\n"
        "7. Write a final PARITY_CHECK.md at the top level, merging every "
        f"per-module PARITY_CHECK_*.md in {output_dir} into one list - every "
        "item across every module, with DONE/MISSING status re-verified "
        "after assembly (something that was DONE pre-assembly can break "
        "during wiring). If anything is MISSING, fix it before finishing.\n"
    )