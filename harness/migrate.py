"""Migration step: analyze findings -> ask Claude to rewrite into a new
stack -> push to a NEW output repo (never touches the input repo)."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
import time
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
    Reads the input repo (read-only), asks Claude Code to produce a
    modernized version of ONE scoped module in a fresh output folder,
    then pushes that output folder to a brand-new GitHub repo. Input
    repo is untouched.

    Returns (repo_url, local_output_path).
    """
    output_dir = Path(tempfile.mkdtemp(prefix="migration_output_"))

    prompt = (
        f"You have been granted read access to a legacy application at: {input_repo_path}\n\n"
        f"This is GLPI (a large IT asset/helpdesk PHP app). DO NOT attempt to migrate "
        f"the entire application — that is out of scope.\n\n"
        f"ONLY migrate this specific module/feature: {scope}\n\n"
        f"Your task:\n"
        f"1. Find the code for ONLY the '{scope}' module/feature at {input_repo_path}\n"
        f"2. Rewrite ONLY that module to: {target_stack}\n"
        f"3. Write the new code into your current working directory (empty right now)\n"
        f"4. Do NOT modify {input_repo_path} — read-only reference only\n"
        f"5. This must be a working, runnable slice — not just scaffolding\n\n"
        f"Known vulnerabilities/issues to fix:\n{findings_md}\n\n"
        f"Start by locating the '{scope}' related files at {input_repo_path}, "
        f"then write the migrated version here."
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

    written_files = [p for p in output_dir.rglob("*") if p.is_file() and ".git" not in p.parts]
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
    run_git("commit", "-m", "Initial migrated version")
    run_git("branch", "-M", "main")
    run_git("remote", "add", "origin", repo_url)
    run_git("push", "-u", "origin", "main")

    return repo_url, str(output_dir)