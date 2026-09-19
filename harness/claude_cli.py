"""
Thin wrapper around the Claude Code CLI so the rest of the harness
can call it programmatically: pass a prompt, get text back.

Assumes the `claude` CLI is installed and authenticated
(claude login) on this machine.
"""

from __future__ import annotations

import subprocess
import shlex
from dataclasses import dataclass


@dataclass
class ClaudeCLIResult:
    success: bool
    stdout: str
    stderr: str
    returncode: int


def run_claude_prompt(
    prompt: str,
    cwd: str | None = None,
    timeout: int = 300,
    extra_args: list[str] | None = None,
) -> ClaudeCLIResult:
    """
    Run a single non-interactive prompt through the Claude Code CLI.

    Uses `claude -p "<prompt>"` (print mode: runs once and exits,
    instead of opening the interactive REPL). Adjust `extra_args`
    if your installed CLI version uses different flags.
    """
    args = ["claude", "-p", prompt]
    if extra_args:
        args.extend(extra_args)

    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return ClaudeCLIResult(
            success=proc.returncode == 0,
            stdout=proc.stdout.strip(),
            stderr=proc.stderr.strip(),
            returncode=proc.returncode,
        )
    except FileNotFoundError:
        return ClaudeCLIResult(
            success=False,
            stdout="",
            stderr=(
                "`claude` CLI not found on PATH. Install it first: "
                "npm install -g @anthropic-ai/claude-code (or see "
                "official Claude Code install docs) and run `claude login`."
            ),
            returncode=127,
        )
    except subprocess.TimeoutExpired:
        return ClaudeCLIResult(
            success=False,
            stdout="",
            stderr=f"Claude CLI call timed out after {timeout}s.",
            returncode=-1,
        )


def check_claude_available() -> ClaudeCLIResult:
    """Quick sanity check: is the CLI installed and authenticated?"""
    try:
        proc = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return ClaudeCLIResult(
            success=proc.returncode == 0,
            stdout=proc.stdout.strip(),
            stderr=proc.stderr.strip(),
            returncode=proc.returncode,
        )
    except FileNotFoundError:
        return ClaudeCLIResult(
            success=False,
            stdout="",
            stderr="`claude` CLI not found on PATH.",
            returncode=127,
        )
    except subprocess.TimeoutExpired:
        return ClaudeCLIResult(
            success=False, stdout="", stderr="Version check timed out.", returncode=-1
        )
