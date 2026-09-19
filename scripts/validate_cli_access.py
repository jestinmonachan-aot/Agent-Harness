"""
Validation script #1: confirm we can call Claude Code CLI
programmatically and get a real response back.

Run:  python scripts/validate_cli_access.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.claude_cli import check_claude_available, run_claude_prompt


def main():
    print("1) Checking `claude` CLI is installed and authenticated...")
    check = check_claude_available()
    if not check.success:
        print("   FAILED:", check.stderr)
        print("   -> Install: npm install -g @anthropic-ai/claude-code")
        print("   -> Then run: claude login")
        return
    print("   OK:", check.stdout)

    print("\n2) Sending a test prompt...")
    result = run_claude_prompt("Reply with exactly: HARNESS_OK")
    if result.success and "HARNESS_OK" in result.stdout:
        print("   OK — CLI responded correctly:", result.stdout)
    else:
        print("   FAILED. stdout:", result.stdout, "| stderr:", result.stderr)


if __name__ == "__main__":
    main()
