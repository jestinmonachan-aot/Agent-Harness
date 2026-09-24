"""Migration step: copy the input repo, ask Claude to apply targeted
fixes to the app (one scoped module, or the entire application), then
push to a NEW output repo (never touches the input repo)."""

from __future__ import annotations
import re
import subprocess
import shutil
import tempfile
import time
from pathlib import Path

from harness.claude_cli import run_claude_prompt


def create_github_repo(repo_name: str, private: bool = True) -> str:
    """Create a new empty GitHub repo via `gh` CLI. Returns its clone URL.
    Requires `gh auth login` already done."""
    visibility = "--private" if private else "--public"
    proc = subprocess.run(
        ["gh", "repo", "create", repo_name, visibility],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh repo create failed: {proc.stderr}")
    url_line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    return url_line or f"https://github.com/<your-user>/{repo_name}"


def _build_prompt(input_repo_path: str, findings_md: str, scope: str | None) -> str:
    full_app = not scope or scope.strip().lower() in ("full app", "entire application", "all", "*")
    scope_desc = "the ENTIRE application" if full_app else f"ONLY the '{scope}' feature/module"

    return (
        f"You are migrating {scope_desc} of a legacy application to a "
        f"modern tech stack of YOUR CHOOSING. Analyze the codebase and pick "
        f"the best-fit modern stack (e.g. FastAPI+React, Django+Vue, "
        f"Spring Boot+Angular, etc.) based on what the app actually needs "
        f"(current language ecosystem, team-friendliness, app complexity). "
        f"This is not optional — you must choose one and justify it.\n\n"
        f"Legacy source (read-only, do not modify): {input_repo_path}\n\n"
        f"Known vulnerabilities/issues to fix during the rewrite:\n{findings_md}\n\n"
        "REQUIREMENTS:\n"
        "1. Analyze the legacy code, pick the best modern stack, and write "
        "STACK_DECISION.md explaining your choice and reasoning in plain, "
        "clear language (a paragraph or two).\n"
        "2. Reimplement the scoped app/feature from scratch in the chosen "
        "stack — new folder structure, not a patch on the legacy code.\n"
        "3. Preserve the exact existing UX (fields, flows, validation, look).\n"
        "4. Fix the listed vulnerabilities as part of the rewrite.\n"
        "5. Do NOT modify anything at the legacy source path.\n"
        "6. Write MIGRATION_NOTES.md summarizing what was migrated.\n"
        "7. As the LAST LINE of your final response, output exactly:\n"
        "STACK_CHOSEN: <short stack name>\n"
    )


def migrate_and_push(
    input_repo_path: str,
    findings_md: str,
    scope: str | None,
    timeout: int | None = None,
) -> tuple[str, str, str, str]:
    """Returns (repo_url, local_output_path, stack_chosen, stack_reasoning)."""
    output_dir = Path(tempfile.mkdtemp(prefix="migration_output_"))
    full_app = not scope or scope.strip().lower() in ("full app", "entire application", "all", "*")
    prompt = _build_prompt(input_repo_path, findings_md, scope)
    effective_timeout = timeout if timeout is not None else (3600 if full_app else 1800)

    result = run_claude_prompt(
        prompt, cwd=str(output_dir), timeout=effective_timeout,
        extra_args=["--add-dir", str(input_repo_path)],
    )
    if not result.success:
        raise RuntimeError(f"Migration prompt failed. returncode={result.returncode}, stdout={result.stdout[:500]!r}")

    stack_match = re.search(r"STACK_CHOSEN:\s*(.+)", result.stdout)
    stack_chosen = stack_match.group(1).strip() if stack_match else "unknown"

    decision_file = output_dir / "STACK_DECISION.md"
    stack_reasoning = decision_file.read_text(encoding="utf-8") if decision_file.exists() else ""

    written_files = [p for p in output_dir.rglob("*") if p.is_file() and ".git" not in p.parts]
    if not written_files:
        raise RuntimeError(f"Claude Code produced no output files. stdout: {result.stdout[:500]}")

    branch_name = f"migration-{int(time.time())}"
    target_repo = "https://github.com/jestinmonachan-aot/harness-new-test.git"

    def run_git(*args):
        proc = subprocess.run(["git", *args], cwd=output_dir, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr}")

    run_git("init")
    run_git("add", ".")
    run_git("commit", "-m", f"Modernized migration ({stack_chosen}): {scope or 'full app'}")
    run_git("branch", "-M", branch_name)
    run_git("remote", "add", "origin", target_repo)
    run_git("push", "-u", "origin", branch_name)

    repo_url = f"https://github.com/jestinmonachan-aot/harness-new-test/tree/{branch_name}"
    return repo_url, str(output_dir), stack_chosen, stack_reasoning