"""
Streamlit UI for the agent harness workflow:
  1. User pastes a GitHub repo link (the microservice to analyze)
  2. We clone it
  3. We call Claude Code CLI to analyze vulnerabilities
  4. We parse + display the result, and save it as a .md report
  5. Optionally migrate to a new stack -> push to new repo
  6. Optionally dockerize + deploy -> show URL

Each step runs in a fully detached worker process (see harness/worker.py +
harness/job_runner.py) so a Streamlit crash/OOM/sleep cannot kill an
in-flight Claude CLI call. This script never blocks on the CLI itself —
it launches a step, then polls SQLite via st.rerun() until that step's
status flips to 'done' or 'error'.
"""

import time
from pathlib import Path

import streamlit as st

from harness import db, job_runner
from harness.claude_cli import check_claude_available

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

for key in ["job_id", "repo_url"]:
    if key not in st.session_state:
        st.session_state[key] = None

repo_url = st.text_input(
    "GitHub repo URL",
    placeholder="https://github.com/org/glpi",
)

run_clicked = st.button("Run analysis", type="primary", disabled=not repo_url)

if run_clicked:
    job_id = job_runner.resume_or_new_job(repo_url)
    existing_status = job_runner.get_step_status(job_id, "analyze")
    if not existing_status or existing_status["status"] != "done":
        job_runner.launch_step(job_id, "analyze", {"repo_url": repo_url})
    st.session_state.job_id = job_id
    st.session_state.repo_url = repo_url

job_id = st.session_state.job_id

if job_id is not None:
    job = db.get_job(job_id)
    analyze_status = job_runner.get_step_status(job_id, "analyze")

    if analyze_status and analyze_status["status"] == "running":
        st.info("Analyzing with Claude Code... this page refreshes automatically.")
        with st.expander("Worker log (live)"):
            st.code(job_runner.get_worker_log(job_id, "analyze") or "(no output yet)")
        time.sleep(2)
        st.rerun()

    elif analyze_status and analyze_status["status"] == "error":
        st.error(f"Analysis failed:\n\n```\n{analyze_status['error'][-2000:]}\n```")

    elif analyze_status and analyze_status["status"] == "done":
        findings = job["findings"]
        repo_path = job["repo_path"]

        st.subheader("Findings")
        st.markdown(findings)

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

        migrate_status = job_runner.get_step_status(job_id, "migrate")
        migrate_running = bool(migrate_status and migrate_status["status"] == "running")

        if st.button("Run migration", key="run_migration_btn", disabled=migrate_running):
            job_runner.launch_step(
                job_id,
                "migrate",
                {
                    "repo_path": repo_path,
                    "findings": findings,
                    "target_stack": target_stack,
                    "scope": scope_input,
                    "output_repo_name": output_repo_name,
                },
            )
            st.rerun()

        if migrate_status:
            if migrate_status["status"] == "running":
                st.info("Migrating... this page refreshes automatically.")
                with st.expander("Worker log (live)"):
                    st.code(job_runner.get_worker_log(job_id, "migrate") or "(no output yet)")
                time.sleep(2)
                st.rerun()
            elif migrate_status["status"] == "error":
                st.error(f"Migration failed:\n\n```\n{migrate_status['error'][-2000:]}\n```")
            elif migrate_status["status"] == "done":
                out_url = migrate_status["result"]["repo_url"]
                st.success(f"Pushed to {out_url}")

        st.divider()
        st.subheader("Deploy with Docker")

        # Deploy the migrated output if it exists, otherwise the original repo
        deploy_target = repo_path
        if migrate_status and migrate_status["status"] == "done":
            deploy_target = migrate_status["result"]["repo_path"]

        deploy_status = job_runner.get_step_status(job_id, "deploy")
        deploy_running = bool(deploy_status and deploy_status["status"] == "running")

        if st.button("Run deployment", key="run_deployment_btn", disabled=deploy_running):
            job_runner.launch_step(
                job_id,
                "deploy",
                {"target_path": deploy_target, "container_name": "migrated_app"},
            )
            st.rerun()

        if deploy_status:
            if deploy_status["status"] == "running":
                st.info("Deploying... this page refreshes automatically.")
                with st.expander("Worker log (live)"):
                    st.code(job_runner.get_worker_log(job_id, "deploy") or "(no output yet)")
                time.sleep(2)
                st.rerun()
            elif deploy_status["status"] == "error":
                st.error(f"Deployment failed:\n\n```\n{deploy_status['error'][-2000:]}\n```")
            elif deploy_status["status"] == "done":
                app_url = deploy_status["result"]["app_url"]
                st.success(f"App live at: {app_url}")
                st.markdown(f"[Open App]({app_url})")