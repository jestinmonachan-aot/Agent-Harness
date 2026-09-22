"""
Streamlit UI for the agent harness workflow:
  1. User pastes a GitHub repo link (the microservice to analyze)
  2. We clone it
  3. We call Claude Code CLI to analyze vulnerabilities
  4. We parse + display the result, and save it as a .md report
  5. Optionally migrate to a new stack -> push to new repo
  6. Optionally dockerize + deploy -> show URL
"""

import streamlit as st
from pathlib import Path

from harness import db
from harness.migrate import migrate_and_push
from harness.deploy import dockerize_and_run
from harness.repo_utils import clone_repo
from harness.claude_cli import run_claude_prompt, check_claude_available
from harness.md_utils import save_analysis_report

st.set_page_config(page_title="Agent Harness — Vulnerability Scan", layout="wide")
db.init_db()

st.title("Legacy App Migration — Vulnerability Scan")
st.caption("Paste a repo link for the target microservice to analyze it with Claude Code.")

with st.sidebar:
    st.subheader("Environment check")
    check = check_claude_available()
    if check.success:
        st.success(f"Claude CLI OK: {check.stdout}")
    else:
        st.error(f"Claude CLI not available: {check.stderr}")

# Persist state across reruns (every button click reruns the whole script)
for key in ["repo_path", "result", "job_id", "repo_url"]:
    if key not in st.session_state:
        st.session_state[key] = None

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
        result = run_claude_prompt(prompt, cwd=str(repo_path), timeout=1200)

        if not result.success:
            status.update(label="Analysis failed", state="error")
            st.error(result.stderr or "Unknown error running Claude CLI.")
            st.stop()

        status.update(label="Done", state="complete")

    report_path = Path("reports") / "latest_analysis.md"
    save_analysis_report(report_path, repo_url, result.stdout)

    job_id = db.create_job(repo_url)
    db.save_findings(job_id, result.stdout)

    # Save to session_state so Migrate/Deploy buttons below can use them
    st.session_state.repo_path = str(repo_path)
    st.session_state.result = result.stdout
    st.session_state.job_id = job_id
    st.session_state.repo_url = repo_url

# Show findings + migrate/deploy sections if we have a completed analysis
if st.session_state.result:
    st.subheader("Findings")
    st.markdown(st.session_state.result)

    report_path = Path("reports") / "latest_analysis.md"
    if report_path.exists():
        st.download_button(
            "Download report (.md)",
            data=report_path.read_text(encoding="utf-8"),
            file_name="vulnerability_report.md",
            mime="text/markdown",
        )

    st.divider()
    st.subheader("Migrate to modern stack")
    target_stack = st.text_input("Target stack", "Python FastAPI + React", key="target_stack")
    scope_input = st.text_input("Which module/feature to migrate (e.g. 'ticketing')", "ticketing", key="scope_input")
    output_repo_name = st.text_input("New output repo name", "migrated-app", key="output_repo_name")

    if st.button("Run migration", key="run_migration_btn"):
        with st.status("Migrating...", expanded=True):
            try:
                out_url, out_path = migrate_and_push(
                    st.session_state.repo_path,
                    st.session_state.result,
                    target_stack,
                    scope_input,
                    output_repo_name,
                )
                db.save_migration(st.session_state.job_id, out_url)
                st.success(f"Pushed to {out_url}")
                st.session_state.output_repo_url = out_url
                st.session_state.output_repo_path = out_path
            except Exception as e:
                st.error(str(e))

    st.divider()
    st.subheader("Deploy with Docker")
    if st.button("Run deployment", key="run_deployment_btn"):
        with st.status("Deploying...", expanded=True):
            try:
                target_path = st.session_state.get("output_repo_path", st.session_state.repo_path)
                app_url = dockerize_and_run(target_path, container_name="migrated_app")
                db.save_deployment(st.session_state.job_id, app_url)
                st.success(f"App live at: {app_url}")
                st.markdown(f"[Open App]({app_url})")
            except Exception as e:
                st.error(str(e))