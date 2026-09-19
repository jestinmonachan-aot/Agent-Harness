"""
Validation script #2: confirm we can programmatically read, write,
and append to .md files (needed for CLAUDE.md control + reports).

Run:  python scripts/validate_md_access.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.md_utils import write_md, read_md, append_section, save_analysis_report


def main():
    tmp_dir = Path(tempfile.mkdtemp(prefix="md_validate_"))
    test_file = tmp_dir / "CLAUDE_test.md"

    print("1) Writing a test .md file...")
    write_md(test_file, "# Test CLAUDE.md\n\nInitial content.")
    print("   OK:", test_file)

    print("\n2) Reading it back...")
    content = read_md(test_file)
    assert "Initial content." in content
    print("   OK — content matches")

    print("\n3) Appending a section...")
    append_section(test_file, "Rules", "- Never touch production configs.")
    content = read_md(test_file)
    assert "## Rules" in content
    print("   OK — section appended")

    print("\n4) Writing a sample analysis report...")
    report_file = tmp_dir / "report.md"
    save_analysis_report(
        report_file,
        repo_url="https://github.com/example/legacy-app",
        findings="- No findings yet (this is a validation run).",
    )
    print("   OK:", report_file)

    print(f"\nAll checks passed. Test files at: {tmp_dir}")


if __name__ == "__main__":
    main()
