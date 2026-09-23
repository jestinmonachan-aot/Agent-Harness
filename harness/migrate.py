"""Migration step: copy the input repo, ask Claude to apply targeted
fixes only to legacy/vulnerable parts, then push to a NEW output repo
(never touches the input repo)."""

from __future__ import annotations

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


def migrate_and_push(
    input_repo_path: str,
    findings_md: str,
    target_stack: str,
    scope: str,
    output_repo_name: str,
) -> tuple[str, str]:
    """
    Rewrites ONE scoped feature/module of the legacy app into a new
    tech stack, while preserving the exact existing UX (fields, flows,
    validation, look-and-feel) and fixing known vulnerabilities.
    Pushes the result to a brand-new GitHub repo. Input repo is never
    modified.

    Returns (repo_url, local_output_path).
    """
    output_dir = Path(tempfile.mkdtemp(prefix="migration_output_"))

    prompt = (
        f"You are migrating ONE scoped feature of a legacy application to "
        f"a new tech stack: {target_stack}.\n\n"
        f"Legacy source (read-only, do not modify): {input_repo_path}\n"
        f"Scope: ONLY the '{scope}' feature/module — nothing else.\n\n"
        f"Known vulnerabilities/issues to fix during the rewrite:\n{findings_md}\n\n"
        f"THIS IS NOT A PATCH. You must NOT just add a fix to the existing "
        f"PHP code. You must write entirely NEW code in {target_stack} that "
        f"reimplements this feature from scratch — new backend files, new "
        f"frontend files, in a completely new folder structure. Do NOT edit "
        f"or add to any existing PHP file. Do NOT copy the legacy repo "
        f"structure. Start with a blank {target_stack} project scaffold.\n\n"
        f"CRITICAL REQUIREMENTS:\n"
        f"1. Step 1 — Analyze: read the legacy code for '{scope}' at "
        f"{input_repo_path}. Identify every screen, field, form, validation "
        f"rule, button/action, navigation flow, and displayed data for this "
        f"feature.\n"
        f"2. Step 2 — Preserve UX: the new app must look and behave "
        f"IDENTICALLY to the legacy version for this feature — same fields, "
        f"same labels, same validation rules, same page flow, same visual "
        f"layout (recreate the HTML/CSS to match, not just similar). A user "
        f"should not notice any visual or functional difference.\n"
        f"3. Step 3 — Rewrite in new stack: implement the backend and "
        f"frontend for this feature using {target_stack}. Do not just "
        f"scaffold folders — write complete, working, runnable code: routes, "
        f"models/schemas, business logic, and UI components.\n"
        f"4. Step 4 — Fix vulnerabilities: apply secure coding practices for "
        f"the issues listed above (parameterized queries, input validation, "
        f"no hardcoded secrets, etc.) as part of the rewrite.\n"
        f"5. Do NOT modify anything at {input_repo_path} — read-only "
        f"reference only.\n"
        f"6. Write a MIGRATION_NOTES.md summarizing what was migrated, what "
        f"UX elements were preserved, and what vulnerabilities were fixed.\n\n"
        f"Write all code into your current working directory (empty right "
        f"now). Start by listing/reading the legacy '{scope}' files, then "
        f"build the new-stack version."
    )

    result = run_claude_prompt(
        prompt,
        cwd=str(output_dir),
        timeout=1800,
        extra_args=["--add-dir", str(input_repo_path)],
    )
    if not result.success:
        raise RuntimeError(
            f"Migration prompt failed. returncode={result.returncode}, "
            f"stdout={result.stdout[:500]!r}, stderr={result.stderr[:500]!r}"
        )

    written_files = [
        p for p in output_dir.rglob("*")
        if p.is_file() and ".git" not in p.parts
    ]
    if not written_files:
        raise RuntimeError(
            "Claude Code produced no output files in the migration folder. "
            f"CLI stdout was: {result.stdout[:500]}"
        )

    unique_repo_name = f"{output_repo_name}-{int(time.time())}"
    repo_url = create_github_repo(unique_repo_name)

    def run_git(*args):
        proc = subprocess.run(
            ["git", *args], cwd=output_dir, capture_output=True, text=True
        )
        if proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr}")

    run_git("init")
    run_git("add", ".")
    run_git("commit", "-m", "Modernized migration: new stack, preserved UX, fixed vulnerabilities")
    run_git("branch", "-M", "main")
    run_git("remote", "add", "origin", repo_url)
    run_git("push", "-u", "origin", "main")

    return repo_url, str(output_dir)