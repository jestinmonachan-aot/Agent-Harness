"""
Streamlit UI for the agent harness workflow:
  1. User pastes a GitHub repo link (the microservice to analyze)
  2. We clone it
  3. We call Claude Code CLI to analyze vulnerabilities
  4. We parse + display the result, and save it as a .md report
"""

import streamlit as st
from pathlib import Path

from harness.repo_utils import clone_repo
from harness.claude_cli import run_claude_prompt, check_claude_available
from harness.md_utils import save_analysis_report

st.set_page_config(page_title="Agent Harness — Vulnerability Scan", layout="wide")

st.title("Legacy App Migration — Vulnerability Scan")
st.caption("Paste a repo link for the target microservice to analyze it with Claude Code.")

with st.sidebar:
    st.subheader("Environment check")
    check = check_claude_available()
    if check.success:
        st.success(f"Claude CLI OK: {check.stdout}")
    else:
        st.error(f"Claude CLI not available: {check.stderr}")

repo_url = st.text_input(
    "GitHub repo URL",
    placeholder="https://github.com/org/glpi",
)

run_clicked = st.button("Run analysis", type="primary", disabled=not repo_url)

if run_clicked:
    with st.status("Cloning repo...", expanded=True) as status:
        try:
            repo_path = clone_repo(repo_url)
            st.write(f"Cloned to `{repo_path}`")
        except Exception as e:
            status.update(label="Clone failed", state="error")
            st.error(str(e))
            st.stop()

        status.update(label="Analyzing with Claude Code...")
        prompt = (
            f"You are analyzing the codebase at {repo_path} for a legacy "
            "modernization migration. Identify security vulnerabilities: "
            "outdated dependencies, injection risks, hardcoded secrets, "
            "insecure auth, and anything blocking a safe migration. "
            "Return a concise, structured list of findings."
        )
        result = run_claude_prompt(prompt, cwd=str(repo_path))

        if not result.success:
            status.update(label="Analysis failed", state="error")
            st.error(result.stderr or "Unknown error running Claude CLI.")
            st.stop()

        status.update(label="Done", state="complete")

    st.subheader("Findings")
    st.markdown(result.stdout or "_No output returned._")

    report_path = Path("reports") / "latest_analysis.md"
    save_analysis_report(report_path, repo_url, result.stdout)
    st.download_button(
        "Download report (.md)",
        data=report_path.read_text(encoding="utf-8"),
        file_name="vulnerability_report.md",
        mime="text/markdown",
    )
