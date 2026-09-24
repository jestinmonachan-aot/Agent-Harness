"""Migration step: copy the input repo, ask Claude to apply targeted
fixes to the app (one scoped module, or the entire application), then
push to a NEW output repo (never touches the input repo).

Two paths:

- NARROW scope (e.g. "ticket section"): migrated directly in a single
  Claude Code call - no planning, no knowledge base, no assembly
  overhead. Fast, and the output is deployable on its own.

- FULL APP: large legacy apps (e.g. GLPI) are far too big for one CLI
  call to read, reason about, and faithfully port in a single pass.
  Instead:
    1. PLAN     - Claude reads the repo's structure and proposes a
                  short list of functional modules (tickets, assets,
                  users, ...).
    2. BUILD    - each module is migrated in its own call, in order.
                  Each module first reads KNOWLEDGE_BASE.md (a running
                  summary of decisions made by prior modules: stack,
                  naming/folder conventions, shared models/endpoints
                  already built) so later modules don't re-derive or
                  diverge from earlier ones. Each module also writes
                  EXISTING_UX_INVENTORY_<module>.md, an exhaustive,
                  verbatim list of the legacy fields/flows/labels for
                  that module - this is what keeps names and UX
                  identical to the legacy app. After building, the
                  module appends its own summary onto
                  KNOWLEDGE_BASE.md, and MIGRATION_STATE.json is
                  updated to mark it complete.
    3. ASSEMBLE - one final pass wires all modules into a single
                  coherent app and cross-checks it against every
                  inventory file for consistency.

RESUMABILITY: a long full-app run can hit Claude usage limits or other
transient failures partway through. Progress is persisted to
MIGRATION_STATE.json in the output directory after every completed
module, so a failed run can be resumed (pass the same output_dir back
in as resume_output_dir) without redoing finished modules or
replanning. A failure whose message matches known usage/rate-limit
signatures is raised as UsageLimitError specifically, carrying the
output_dir, so the caller can tell the user "hit a usage limit, resume
when ready" instead of "migration failed" - and can offer a literal
"Resume migration" action rather than "Run migration" from scratch.

Per-module timeouts are kept realistic (not maximally large) - a
timeout is a hard kill switch, not a quality dial: giving a module far
more time than it needs doesn't improve its output, it just delays
noticing a stuck call. The real lever for quality is keeping each
module's scope small enough to comfortably finish, which the planning
step handles by breaking a full app into 3-8 pieces.
"""

from __future__ import annotations
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

from harness.claude_cli import run_claude_prompt

DEFAULT_PLANNING_TIMEOUT = 600
DEFAULT_PER_MODULE_TIMEOUT = 1800
DEFAULT_ASSEMBLY_TIMEOUT = 1200
DEFAULT_NARROW_SCOPE_TIMEOUT = 1800

STATE_FILENAME = "MIGRATION_STATE.json"
KNOWLEDGE_BASE_FILENAME = "KNOWLEDGE_BASE.md"

# Substrings that indicate a Claude usage/rate limit rather than a real
# migration failure. Matched case-insensitively against combined
# stdout+stderr. Kept broad-ish on purpose since exact wording of CLI
# limit messages can change between versions.
USAGE_LIMIT_SIGNATURES = [
    "usage limit", "rate limit", "rate_limit", "quota", "too many requests",
    "429", "resets at", "try again later", "overloaded",
]


class UsageLimitError(RuntimeError):
    """Raised when a Claude CLI call fails in a way that looks like a
    usage/rate limit rather than a genuine migration failure. Carries
    the output_dir so the caller can offer to resume from here instead
    of starting over."""
    def __init__(self, message: str, output_dir: Path):
        super().__init__(message)
        self.output_dir = output_dir


def create_github_repo(repo_name: str, private: bool = True) -> str:
    visibility = "--private" if private else "--public"
    proc = subprocess.run(
        ["gh", "repo", "create", repo_name, visibility],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh repo create failed: {proc.stderr}")
    url_line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    return url_line or f"https://github.com/<your-user>/{repo_name}"


def _is_full_app(scope: str | None) -> bool:
    return not scope or scope.strip().lower() in ("full app", "entire application", "all", "*")


def _run_git(cwd: Path, *args):
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr}")


def _looks_like_usage_limit(stdout: str, stderr: str) -> bool:
    combined = f"{stdout}\n{stderr}".lower()
    return any(sig in combined for sig in USAGE_LIMIT_SIGNATURES)


# ---------------------------------------------------------------------------
# State persistence (for resumability)
# ---------------------------------------------------------------------------

def _load_state(output_dir: Path) -> dict | None:
    state_path = output_dir / STATE_FILENAME
    if not state_path.exists():
        return None
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_state(output_dir: Path, state: dict) -> None:
    state_path = output_dir / STATE_FILENAME
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# NARROW SCOPE: single direct migration, no planning/knowledge-base overhead
# ---------------------------------------------------------------------------

def _build_direct_prompt(input_repo_path: str, findings_md: str, scope: str) -> str:
    return (
        f"You are migrating ONLY the '{scope}' feature/module of a legacy "
        f"application to a modern tech stack of YOUR CHOOSING. Analyze the "
        f"codebase and pick the best-fit modern stack (e.g. FastAPI+React, "
        f"Django+Vue, Spring Boot+Angular, etc.) based on what the app "
        f"actually needs. This is not optional - choose one and justify it.\n\n"
        f"Legacy source (read-only, do not modify): {input_repo_path}\n\n"
        f"Known vulnerabilities/issues to fix during the rewrite:\n{findings_md}\n\n"
        "REQUIREMENTS:\n"
        f"1. FIRST, before writing any new code: read the legacy source files "
        f"relevant to '{scope}'. Write EXISTING_UX_INVENTORY.md listing, "
        "verbatim from the source: every field name, its type/options, "
        "validation rules, default values, and every user-facing flow/action "
        "for this scope. Do not skip fields because they seem minor.\n"
        "2. Write STACK_DECISION.md explaining your stack choice and "
        "reasoning in plain, clear language (a paragraph or two).\n"
        "3. Reimplement this scope from scratch in the chosen stack, using "
        "EXISTING_UX_INVENTORY.md as the field/flow spec - every field and "
        "flow listed there MUST appear in the new implementation, with the "
        "same labels/names and options as the legacy app. Do not invent, "
        "drop, or simplify fields.\n"
        "4. Fix the listed vulnerabilities as part of the rewrite.\n"
        "5. Do NOT modify anything at the legacy source path.\n"
        "6. Write MIGRATION_NOTES.md summarizing what was migrated.\n"
        "7. As the LAST LINE of your final response, output exactly:\n"
        "STACK_CHOSEN: <short stack name>\n"
    )


def _migrate_narrow(
    input_repo_path: str, output_dir: Path, findings_md: str, scope: str, timeout: int,
) -> str:
    prompt = _build_direct_prompt(input_repo_path, findings_md, scope)
    result = run_claude_prompt(
        prompt, cwd=str(output_dir), timeout=timeout,
        extra_args=["--add-dir", str(input_repo_path)],
    )
    if not result.success:
        if _looks_like_usage_limit(result.stdout, result.stderr):
            raise UsageLimitError(
                f"Hit what looks like a Claude usage/rate limit during migration: "
                f"{result.stderr[:300] or result.stdout[:300]}",
                output_dir,
            )
        raise RuntimeError(f"Migration prompt failed. returncode={result.returncode}, stdout={result.stdout[:500]!r}")
    stack_match = re.search(r"STACK_CHOSEN:\s*(.+)", result.stdout)
    return stack_match.group(1).strip() if stack_match else "unknown"


# ---------------------------------------------------------------------------
# FULL APP - step 1: plan the module breakdown
# ---------------------------------------------------------------------------

def _build_planning_prompt(input_repo_path: str) -> str:
    return (
        "You are planning a migration of the ENTIRE application to a modern "
        f"stack. Legacy source (read-only): {input_repo_path}\n\n"
        "Explore the codebase structure (directory names, controller/route "
        "files, DB schema) enough to identify the natural functional modules "
        "of the app - the way a user or admin would describe its distinct "
        "feature areas (e.g. 'tickets', 'assets', 'user management', "
        "'authentication', 'reporting'). Keep the list to what's actually "
        "distinct: typically 3-8 modules. Keep each module SMALL enough that "
        "it could realistically be read and reimplemented within about 30 "
        "minutes of focused work - split a large area into two modules "
        "rather than proposing one oversized module.\n\n"
        "Order the list so that foundational/shared modules (auth, core "
        "data model, users) come BEFORE modules that depend on them.\n\n"
        "Respond with ONLY a JSON array, nothing else, no markdown fences:\n"
        '[{"id": "auth", "description": "Login, sessions, permissions"}, '
        '{"id": "tickets", "description": "Ticket creation, listing, and '
        'lifecycle"}, ...]\n'
    )


def _plan_modules(input_repo_path: str, timeout: int) -> list[dict]:
    print("[migrate] Planning module breakdown...", flush=True)
    prompt = _build_planning_prompt(input_repo_path)
    result = run_claude_prompt(
        prompt, cwd=str(input_repo_path), timeout=timeout,
        extra_args=["--add-dir", str(input_repo_path)],
    )
    if not result.success:
        # Planning failures aren't usage-limit-resumable in a meaningful way
        # (nothing has been written yet) - just fail clearly.
        raise RuntimeError(f"Module planning failed. returncode={result.returncode}, stdout={result.stdout[:500]!r}")

    match = re.search(r"\[.*\]", result.stdout, re.DOTALL)
    if match:
        try:
            modules = json.loads(match.group(0))
            if isinstance(modules, list) and modules:
                return modules
        except json.JSONDecodeError:
            pass
    return [{"id": "full_app", "description": "Entire application"}]


# ---------------------------------------------------------------------------
# FULL APP - step 2: build each module
# ---------------------------------------------------------------------------

def _build_module_prompt(
    input_repo_path: str,
    output_dir: Path,
    module: dict,
    findings_md: str,
    is_first_module: bool,
) -> str:
    module_id = module["id"]
    module_desc = module.get("description", module_id)
    kb_path = output_dir / KNOWLEDGE_BASE_FILENAME

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
        f"Known vulnerabilities/issues to fix if relevant to this module:\n{findings_md}\n\n"
        f"{knowledge_base_instruction}\n"
        "REQUIREMENTS:\n"
        f"1. Read the legacy source files relevant to the '{module_id}' module "
        f"(search front/, ajax/, src/, install/mysql schema, etc. for matching "
        f"controllers/forms/models). Write EXISTING_UX_INVENTORY_{module_id}.md "
        "listing, verbatim from the source: every field name, its type/options, "
        "validation rules, default values, and every user-facing flow/action "
        "for this module. Do not skip fields because they seem minor - an "
        "incomplete inventory means an incomplete migration.\n"
        f"2. Reimplement the '{module_id}' module using "
        f"EXISTING_UX_INVENTORY_{module_id}.md as the field/flow spec - every "
        "field and flow listed there MUST appear in the new implementation, "
        "with the SAME labels/names and options as the legacy app (overall "
        "UI/UX should feel identical to the legacy app, just modernized). Do "
        "not invent, drop, or simplify fields. Follow the stack and "
        "conventions already established (see above).\n"
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
        "7. As the LAST LINE of your final response, output exactly:\n"
        "STACK_CHOSEN: <short stack name>\n"
    )


def _migrate_module(
    input_repo_path: str,
    output_dir: Path,
    module: dict,
    findings_md: str,
    is_first_module: bool,
    timeout: int,
) -> str:
    module_id = module["id"]
    print(f"[migrate] Building module: {module_id} ({module.get('description', module_id)})", flush=True)
    prompt = _build_module_prompt(
        input_repo_path, output_dir, module, findings_md, is_first_module,
    )
    result = run_claude_prompt(
        prompt, cwd=str(output_dir), timeout=timeout,
        extra_args=["--add-dir", str(input_repo_path)],
    )
    if not result.success:
        if _looks_like_usage_limit(result.stdout, result.stderr):
            raise UsageLimitError(
                f"Hit what looks like a Claude usage/rate limit while migrating "
                f"module '{module['id']}': {result.stderr[:300] or result.stdout[:300]}. "
                f"Modules completed before this one are safely saved in "
                f"{output_dir} - resume to continue from here.",
                output_dir,
            )
        raise RuntimeError(
            f"Migration of module '{module['id']}' failed. "
            f"returncode={result.returncode}, stdout={result.stdout[:500]!r}"
        )
    stack_match = re.search(r"STACK_CHOSEN:\s*(.+)", result.stdout)
    stack_result = stack_match.group(1).strip() if stack_match else "unknown"
    print(f"[migrate] Module '{module_id}' done.", flush=True)
    return stack_result


# ---------------------------------------------------------------------------
# FULL APP - step 3: assemble modules into one coherent app
# ---------------------------------------------------------------------------

def _build_assembly_prompt(output_dir: Path, modules: list[dict], chosen_stack: str) -> str:
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
        "listed field/flow/label is actually present and named consistently "
        "with the legacy app. Fix anything that drifted.\n"
        "5. Write a top-level README.md describing the app, its modules, and "
        "how to run it.\n"
        "6. Do not remove or regress functionality already migrated per "
        "module - only integrate and fix conflicts.\n"
    )


def _assemble_modules(output_dir: Path, modules: list[dict], chosen_stack: str, timeout: int) -> None:
    if len(modules) <= 1:
        return
    print(f"[migrate] Assembling {len(modules)} modules into final app...", flush=True)
    prompt = _build_assembly_prompt(output_dir, modules, chosen_stack)
    result = run_claude_prompt(prompt, cwd=str(output_dir), timeout=timeout)
    if not result.success:
        if _looks_like_usage_limit(result.stdout, result.stderr):
            raise UsageLimitError(
                f"Hit what looks like a Claude usage/rate limit during the final "
                f"assembly pass. All modules are already migrated and saved in "
                f"{output_dir} - resume to retry just the assembly step.",
                output_dir,
            )
        raise RuntimeError(
            f"Assembly pass failed (per-module output is still on disk at "
            f"{output_dir}, nothing was lost). returncode={result.returncode}, "
            f"stdout={result.stdout[:500]!r}"
        )


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def migrate_and_push(
    input_repo_path: str,
    findings_md: str,
    scope: str | None,
    timeout: int | None = None,
    planning_timeout: int = DEFAULT_PLANNING_TIMEOUT,
    per_module_timeout: int = DEFAULT_PER_MODULE_TIMEOUT,
    assembly_timeout: int = DEFAULT_ASSEMBLY_TIMEOUT,
    resume_output_dir: str | None = None,
) -> tuple[str, str, str, str]:
    """Returns (repo_url, local_output_path, stack_chosen, stack_reasoning).

    Narrow scope -> single direct migration call. Not resumable (it's one
    call; if it fails, just re-run it - there's nothing partial to resume).

    Full app -> plan modules, migrate each in order, then assemble.
    RESUMABLE: if a module or the assembly step hits what looks like a
    Claude usage/rate limit, a UsageLimitError is raised carrying
    `output_dir`. Call this function again with
    resume_output_dir=<that path> to continue from the next incomplete
    step - already-completed modules are skipped, planning is not
    redone, and nothing already written is touched.

    `timeout`, if explicitly passed, overrides per_module_timeout only,
    for backward compatibility with existing callers.
    """
    if timeout is not None:
        per_module_timeout = timeout

    full_app = _is_full_app(scope)

    if not full_app:
        # Narrow scope: no state/resume machinery, matches original behavior.
        output_dir = Path(tempfile.mkdtemp(prefix="migration_output_"))
        chosen_stack = _migrate_narrow(
            input_repo_path, output_dir, findings_md, scope, DEFAULT_NARROW_SCOPE_TIMEOUT,
        )
        modules = [{"id": scope.strip().lower().replace(" ", "_"), "description": scope}]
    else:
        if resume_output_dir:
            output_dir = Path(resume_output_dir)
            state = _load_state(output_dir)
            if state is None:
                raise RuntimeError(
                    f"Asked to resume from {output_dir} but no {STATE_FILENAME} "
                    "was found there - can't tell what's already done. Start a "
                    "fresh migration instead."
                )
            modules = state["modules"]
            completed_ids = set(state.get("completed_module_ids", []))
            chosen_stack = state.get("chosen_stack", "unknown")
            assembly_done = state.get("assembly_done", False)
        else:
            output_dir = Path(tempfile.mkdtemp(prefix="migration_output_"))
            modules = _plan_modules(input_repo_path, planning_timeout)
            completed_ids = set()
            chosen_stack = "unknown"
            assembly_done = False
            _save_state(output_dir, {
                "scope": scope, "modules": modules,
                "completed_module_ids": [], "chosen_stack": "unknown",
                "assembly_done": False,
            })

        print(f"[migrate] Plan: {len(modules)} module(s) - {[m['id'] for m in modules]}", flush=True)
        if completed_ids:
            print(f"[migrate] Already completed (will skip): {sorted(completed_ids)}", flush=True)

        for i, module in enumerate(modules):
            if module["id"] in completed_ids:
                continue  # already done in a prior attempt - skip, don't re-run
            chosen_stack = _migrate_module(
                input_repo_path, output_dir, module, findings_md,
                is_first_module=(i == 0), timeout=per_module_timeout,
            )
            completed_ids.add(module["id"])
            _save_state(output_dir, {
                "scope": scope, "modules": modules,
                "completed_module_ids": sorted(completed_ids),
                "chosen_stack": chosen_stack, "assembly_done": False,
            })

        if not assembly_done:
            _assemble_modules(output_dir, modules, chosen_stack, assembly_timeout)
            _save_state(output_dir, {
                "scope": scope, "modules": modules,
                "completed_module_ids": sorted(completed_ids),
                "chosen_stack": chosen_stack, "assembly_done": True,
            })

    decision_file = output_dir / "STACK_DECISION.md"
    stack_reasoning = decision_file.read_text(encoding="utf-8") if decision_file.exists() else ""

    written_files = [
        p for p in output_dir.rglob("*")
        if p.is_file() and ".git" not in p.parts and p.name != STATE_FILENAME
    ]
    if not written_files:
        raise RuntimeError("Claude Code produced no output files.")

    branch_name = f"migration-{int(time.time())}"
    target_repo = "https://github.com/jestinmonachan-aot/harness-new-test.git"

    _run_git(output_dir, "init")
    _run_git(output_dir, "add", ".")
    module_summary = ", ".join(m["id"] for m in modules)
    _run_git(output_dir, "commit", "-m", f"Modernized migration ({chosen_stack}): {scope or 'full app'} [{module_summary}]")
    _run_git(output_dir, "branch", "-M", branch_name)
    _run_git(output_dir, "remote", "add", "origin", target_repo)
    _run_git(output_dir, "push", "-u", "origin", branch_name)

    repo_url = f"https://github.com/jestinmonachan-aot/harness-new-test/tree/{branch_name}"
    return repo_url, str(output_dir), chosen_stack, stack_reasoning