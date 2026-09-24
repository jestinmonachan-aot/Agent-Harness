"""
Streamlit UI for the agent harness workflow:
  1. User selects analysis skills, pastes a GitHub repo link
  2. We clone it
  3. We call Claude Code CLI to analyze it (focused on selected skills)
  4. We parse structured findings + display them, and generate a PDF report
  5. Migrate: Claude picks the best modern stack itself, migrates,
     pushes to AOT-Technologies/harness-new (new branch per migration).
     Full-app migrations are done module-by-module with a resumable
     state file, so a Claude usage/rate limit mid-run can be resumed
     instead of starting over.
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
from harness.skills import SKILLS
from harness.md_utils import generate_pdf_report

# Marker prefix worker.py writes into the stored error text when a
# migration failure looks like a Claude usage/rate limit rather than a
# genuine failure. Must match harness.worker.USAGE_LIMIT_MARKER.
USAGE_LIMIT_MARKER = "RESUMABLE_USAGE_LIMIT::"

st.set_page_config(page_title="Agent Harness — Vulnerability Scan", layout="wide")
db.init_db()

st.title("Legacy App Migration — Vulnerability Scan")
st.caption("Select skills, paste a repo link, and analyze it with Claude Code.")

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

st.subheader("Select analysis skills")
st.caption("Selected skills get deep focus. Unselected skills still get a lighter pass — nothing is fully ignored.")
skill_ids = list(SKILLS.keys())
skill_cols = st.columns(len(skill_ids))
selected_skills = []
for col, skill_id in zip(skill_cols, skill_ids):
    with col:
        default_checked = True
        if st.checkbox(SKILLS[skill_id]["label"], value=default_checked, key=f"skill_{skill_id}"):
            selected_skills.append(skill_id)

repo_url = st.text_input(
    "GitHub repo URL",
    placeholder="https://github.com/org/glpi",
)

run_clicked = st.button("Run analysis", type="primary", disabled=not repo_url)

if run_clicked:
    job_id = job_runner.resume_or_new_job(repo_url)
    existing_status = job_runner.get_step_status(job_id, "analyze")
    if not existing_status or existing_status["status"] != "done":
        job_runner.launch_step(job_id, "analyze", {"repo_url": repo_url, "skills": selected_skills})
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
        result_data = analyze_status["result"]
        findings = result_data["findings"]
        repo_path = result_data["repo_path"]
        pdf_path = result_data.get("pdf_path")
        skills_used = result_data.get("skills_used", [])

        st.subheader("Findings")

        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        severity_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢", "info": "⚪"}

        # --- Summary counts, for the non-technical reader ---
        if findings:
            counts = {}
            for f in findings:
                sev = f.get("severity", "info")
                counts[sev] = counts.get(sev, 0) + 1
            summary_parts = [
                f"{severity_icon.get(s, '⚪')} {counts[s]} {s.title()}"
                for s in ["critical", "high", "medium", "low", "info"] if s in counts
            ]
            st.markdown("**Summary:** " + "  ·  ".join(summary_parts))

        if not findings:
            st.info("No issues found.")
        else:
            # --- Group findings by category/skill, so each selected skill gets its own section ---
            grouped = {}
            for f in findings:
                cat = f.get("category", "general")
                grouped.setdefault(cat, []).append(f)

            # Show selected skills first (in the order the user picked them), then any leftover categories
            ordered_categories = [s for s in skills_used if s in grouped] + \
                                  [c for c in grouped if c not in skills_used]

            for cat in ordered_categories:
                cat_label = SKILLS.get(cat, {}).get("label", cat.replace("_", " ").title())
                cat_findings = sorted(grouped[cat], key=lambda x: severity_order.get(x.get("severity", "info"), 4))
                st.markdown(f"### {cat_label} ({len(cat_findings)})")
                for f in cat_findings:
                    icon = severity_icon.get(f.get("severity", "info"), "⚪")
                    title = f.get("title", "Untitled finding")
                    sev = f.get("severity", "info").upper()
                    with st.expander(f"{icon} [{sev}] {title}"):
                        if f.get("location"):
                            st.code(f.get("location"), language=None)
                        st.write(f.get("description", ""))
                        if f.get("recommendation"):
                            st.info(f"**Recommendation:** {f.get('recommendation')}")

        pdf_path = Path("reports") / f"analysis_report_{job_id}.pdf"

        # Regenerate only when findings actually changed, so migrate/deploy
        # polling reruns don't re-render the PDF on every 2s tick.
        findings_key = f"pdf_findings_hash_{job_id}"
        current_hash = hash(str(findings))
        if st.session_state.get(findings_key) != current_hash:
            generate_pdf_report(pdf_path, st.session_state.repo_url, findings, skills_used)
            st.session_state[findings_key] = current_hash

        st.download_button(
            "Download report (PDF)",
            data=pdf_path.read_bytes(),
            file_name="analysis_report.pdf",
            mime="application/pdf",
            key="download_pdf_btn",
        )

        st.divider()
        st.subheader("Migrate to a modern stack")
        st.caption("Claude analyzes the app and chooses the best-fit modern stack itself — you don't pick it.")
        st.caption(
            "For a full-app migration, Claude breaks the app into modules and "
            "migrates them one at a time, then assembles a final app — this "
            "can take a while for large apps, but progress is saved as it goes."
        )
        scope_input = st.text_input(
            "Which module/feature to migrate (leave blank or type 'full app' for the entire application)",
            "full app",
            key="scope_input",
        )

        migrate_status = job_runner.get_step_status(job_id, "migrate")
        migrate_running = bool(migrate_status and migrate_status["status"] == "running")
        migrate_done = bool(migrate_status and migrate_status["status"] == "done")

        # Detect a resumable usage-limit failure from a previous attempt, so
        # we can offer "Resume migration" instead of "Run migration".
        resume_output_dir = None
        if migrate_status and migrate_status["status"] == "error" and migrate_status["error"]:
            err_text = migrate_status["error"]
            if err_text.startswith(USAGE_LIMIT_MARKER):
                rest = err_text[len(USAGE_LIMIT_MARKER):]
                resume_output_dir, _, _human_msg = rest.partition("::")

        if resume_output_dir:
            st.warning(
                "Migration paused — looks like a Claude usage/rate limit was hit "
                "partway through. Progress so far (completed modules) is saved; "
                "you can pick up where it left off instead of starting over."
            )
            if st.button("Resume migration", key="resume_migration_btn"):
                job_runner.launch_step(
                    job_id,
                    "migrate",
                    {
                        "repo_path": repo_path,
                        "findings": findings,
                        "scope": scope_input,
                        "resume_output_dir": resume_output_dir,
                    },
                )
                st.rerun()
        else:
            if st.button("Run migration", key="run_migration_btn", disabled=migrate_running or migrate_done):
                job_runner.launch_step(
                    job_id,
                    "migrate",
                    {
                        "repo_path": repo_path,
                        "findings": findings,
                        "scope": scope_input,
                    },
                )
                st.rerun()

        if migrate_status:
            if migrate_status["status"] == "running":
                st.info("Migrating... this page refreshes automatically.")
                with st.expander("Worker log (live)", expanded=True):
                    st.code(job_runner.get_worker_log(job_id, "migrate") or "(no output yet)")
                time.sleep(2)
                st.rerun()
            elif migrate_status["status"] == "error" and not resume_output_dir:
                st.error(f"Migration failed:\n\n```\n{migrate_status['error'][-2000:]}\n```")
            elif migrate_status["status"] == "done":
                m_result = migrate_status["result"]
                out_url = m_result["repo_url"]
                stack_chosen = m_result.get("stack_chosen", "unknown")
                stack_reasoning = m_result.get("stack_reasoning", "")
                st.success(f"Pushed to {out_url}")
                st.write(f"**Stack chosen by Claude:** {stack_chosen}")
                if stack_reasoning:
                    with st.expander("Why this stack was chosen"):
                        st.markdown(stack_reasoning)

        st.divider()
        st.subheader("Deploy with Docker")

        deploy_target = repo_path
        if migrate_status and migrate_status["status"] == "done":
            deploy_target = migrate_status["result"]["repo_path"]

        deploy_status = job_runner.get_step_status(job_id, "deploy")
        deploy_running = bool(deploy_status and deploy_status["status"] == "running")
        deploy_done = bool(deploy_status and deploy_status["status"] == "done")

        if st.button("Run deployment", key="run_deployment_btn", disabled=deploy_running or deploy_done):
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