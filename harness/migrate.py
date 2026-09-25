"""Migration step: copy the input repo, ask Claude Code to apply
targeted fixes to the app (one scoped module, or the entire
application), then push to a NEW output repo (never touches the input
repo).

TIME CONSTRAINT: a full-app migration must complete within roughly one
hour, so it routes through the SAME single-call path as a narrow-scope
migration (see below), just with an effective scope of "the entire
application" and with the completeness self-audit / runtime
smoke-testing steps skipped (fast_mode) to keep the single call within
budget. Full completeness across every module of a large legacy app is
not realistic in one hour; this deliberately produces a smaller, best-
effort slice rather than an incomplete or timed-out attempt at
everything.

The previous plan -> build-per-module -> assemble pipeline (for
higher-quality, more complete output at the cost of much longer wall-
clock time) is kept in this file, unused, in case the time constraint
is relaxed later - see `_run_full_app_module_pipeline` and the
commented-out call site in `migrate_and_push`.
"""

from __future__ import annotations
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

from harness.claude_cli import run_claude_prompt
from harness.prompts.migration_prompts import (
    build_direct_prompt,
    build_planning_prompt,
    build_module_prompt,
    build_assembly_prompt,
)

DEFAULT_PLANNING_TIMEOUT = 600
DEFAULT_PER_MODULE_TIMEOUT = 2700
DEFAULT_ASSEMBLY_TIMEOUT = 1800
DEFAULT_NARROW_SCOPE_TIMEOUT = 3000  # 50 min, leaving buffer under a 1hr full-app budget

STATE_FILENAME = "MIGRATION_STATE.json"
KNOWLEDGE_BASE_FILENAME = "KNOWLEDGE_BASE.md"

USAGE_LIMIT_SIGNATURES = [
    "usage limit", "session limit", "rate limit", "rate_limit", "quota",
    "too many requests", "429", "resets at", "resets ", "try again later",
    "overloaded",
]


class UsageLimitError(RuntimeError):
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


def _migrate_direct(
    input_repo_path: str, output_dir: Path, findings_md: str, scope: str,
    timeout: int, fast_mode: bool = False,
) -> str:
    prompt = build_direct_prompt(input_repo_path, findings_md, scope, fast_mode=fast_mode)
    result = run_claude_prompt(
        prompt, cwd=str(output_dir), timeout=timeout,
        extra_args=["--add-dir", str(input_repo_path)],
    )
    if not result.success:
        if _looks_like_usage_limit(result.stdout, result.stderr):
            raise UsageLimitError(
                f"Hit what looks like a usage/rate limit during migration: "
                f"{result.stderr[:300] or result.stdout[:300]}",
                output_dir,
            )
        raise RuntimeError(f"Migration prompt failed. returncode={result.returncode}, stdout={result.stdout[:500]!r}")
    stack_match = re.search(r"STACK_CHOSEN:\s*(.+)", result.stdout)
    return stack_match.group(1).strip() if stack_match else "unknown"


# ---------------------------------------------------------------------------
# UNUSED (kept for future re-enable): plan -> build-per-module -> assemble
# ---------------------------------------------------------------------------

def _plan_modules(input_repo_path: str, timeout: int) -> list[dict]:
    print("[migrate] Planning module breakdown...", flush=True)
    prompt = build_planning_prompt(input_repo_path)
    result = run_claude_prompt(
        prompt, cwd=str(input_repo_path), timeout=timeout,
        extra_args=["--add-dir", str(input_repo_path)],
    )
    if not result.success:
        if _looks_like_usage_limit(result.stdout, result.stderr):
            raise RuntimeError(
                f"Hit what looks like a usage/rate limit during module "
                f"planning (nothing written yet, nothing to resume - just retry "
                f"once the limit resets): {result.stderr[:300] or result.stdout[:300]}"
            )
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


def _migrate_module(
    input_repo_path: str, output_dir: Path, module: dict, findings_md: str,
    is_first_module: bool, timeout: int,
) -> str:
    module_id = module["id"]
    print(f"[migrate] Building module: {module_id} ({module.get('description', module_id)})", flush=True)
    prompt = build_module_prompt(input_repo_path, output_dir, module, findings_md, is_first_module)
    result = run_claude_prompt(
        prompt, cwd=str(output_dir), timeout=timeout,
        extra_args=["--add-dir", str(input_repo_path)],
    )
    if not result.success:
        if _looks_like_usage_limit(result.stdout, result.stderr):
            raise UsageLimitError(
                f"Hit what looks like a usage/rate limit while migrating "
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


def _assemble_modules(output_dir: Path, modules: list[dict], chosen_stack: str, timeout: int) -> None:
    if len(modules) <= 1:
        return
    print(f"[migrate] Assembling {len(modules)} modules into final app...", flush=True)
    prompt = build_assembly_prompt(output_dir, modules, chosen_stack)
    result = run_claude_prompt(prompt, cwd=str(output_dir), timeout=timeout)
    if not result.success:
        if _looks_like_usage_limit(result.stdout, result.stderr):
            raise UsageLimitError(
                f"Hit what looks like a usage/rate limit during the final "
                f"assembly pass. All modules are already migrated and saved in "
                f"{output_dir} - resume to retry just the assembly step.",
                output_dir,
            )
        raise RuntimeError(
            f"Assembly pass failed (per-module output is still on disk at "
            f"{output_dir}, nothing was lost). returncode={result.returncode}, "
            f"stdout={result.stdout[:500]!r}"
        )


def _run_full_app_module_pipeline(
    input_repo_path: str, findings_md: str, scope: str | None,
    planning_timeout: int, per_module_timeout: int, assembly_timeout: int,
    resume_output_dir: str | None,
) -> tuple[Path, str, list[dict]]:
    """The higher-quality, longer-running full-app path. Not currently
    called - see module docstring. Kept working and complete so it can
    be re-enabled with a single call-site change in migrate_and_push."""
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
            continue
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

    return output_dir, chosen_stack, modules


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

    Both narrow scope AND full-app scope use a single direct migration
    call, to fit within a ~1 hour time budget. Full-app gets an
    effective scope of "the entire application" and skips the
    completeness self-audit / runtime smoke-test steps (fast_mode) to
    stay within that budget - expect a smaller, best-effort slice
    rather than full coverage of a large legacy app.
    """
    if timeout is not None:
        per_module_timeout = timeout

    full_app = _is_full_app(scope)
    effective_scope = (
        "the entire application - given the strict time budget, focus on "
        "its single most central, highest-value workflow rather than "
        "attempting full breadth"
    ) if full_app else scope

    output_dir = Path(tempfile.mkdtemp(prefix="migration_output_"))
    chosen_stack = _migrate_direct(
        input_repo_path, output_dir, findings_md, effective_scope,
        DEFAULT_NARROW_SCOPE_TIMEOUT, fast_mode=full_app,
    )
    modules = [{
        "id": "full_app" if full_app else scope.strip().lower().replace(" ", "_"),
        "description": effective_scope,
    }]

    decision_file = output_dir / "STACK_DECISION.md"
    stack_reasoning = decision_file.read_text(encoding="utf-8") if decision_file.exists() else ""

    written_files = [
        p for p in output_dir.rglob("*")
        if p.is_file() and ".git" not in p.parts and p.name != STATE_FILENAME
    ]
    if not written_files:
        raise RuntimeError("Migration produced no output files.")

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