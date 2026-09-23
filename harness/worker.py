"""Detached step executor. Invoked as:

    python -m harness.worker <job_id> <step_name> <params_json_path>

Runs entirely outside Streamlit's process tree (see job_runner.launch_step,
which starts this with CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS on
Windows). Writes status to SQLite via db.start_step/finish_step as it goes,
so Streamlit never blocks on this and a Streamlit crash can't kill it.

Params are passed via a JSON file (not argv) because findings/prompts can be
long and argv has length limits on some platforms.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

from harness import db
from harness.repo_utils import clone_repo
from harness.claude_cli import run_claude_prompt
from harness.md_utils import save_analysis_report
from harness.migrate import migrate_and_push
from harness.deploy import dockerize_and_run


def run_analyze(job_id: int, params: dict) -> str:
    repo_url = params["repo_url"]

    repo_path = clone_repo(repo_url)
    db.save_repo_path(job_id, str(repo_path))

    prompt = (
        f"You are analyzing the codebase at {repo_path} for a legacy "
        "modernization migration. Identify security vulnerabilities: "
        "outdated dependencies, injection risks, hardcoded secrets, "
        "insecure auth, and anything blocking a safe migration. "
        "Return a concise, structured list of findings."
    )
    result = run_claude_prompt(prompt, cwd=str(repo_path), timeout=1200)
    if not result.success:
        raise RuntimeError(result.stderr or "Claude CLI analysis failed")

    db.save_findings(job_id, result.stdout)
    report_path = Path("reports") / "latest_analysis.md"
    save_analysis_report(report_path, repo_url, result.stdout)

    return json.dumps({"repo_path": str(repo_path), "findings": result.stdout})


def run_migrate(job_id: int, params: dict) -> str:
    repo_path = params["repo_path"]
    findings = params["findings"]
    target_stack = params["target_stack"]
    scope = params["scope"]
    output_repo_name = params["output_repo_name"]

    out_url, out_path = migrate_and_push(
        repo_path, findings, target_stack, scope, output_repo_name
    )
    db.save_migration(job_id, out_url)

    return json.dumps({"repo_url": out_url, "repo_path": out_path})


def run_deploy(job_id: int, params: dict) -> str:
    target_path = params["target_path"]
    container_name = params["container_name"]

    app_url = dockerize_and_run(target_path, container_name=container_name)
    db.save_deployment(job_id, app_url)

    return json.dumps({"app_url": app_url})


STEP_FUNCS = {
    "analyze": run_analyze,
    "migrate": run_migrate,
    "deploy": run_deploy,
}


def main() -> None:
    if len(sys.argv) != 4:
        print("usage: python -m harness.worker <job_id> <step_name> <params_json_path>", file=sys.stderr)
        sys.exit(2)

    job_id = sys.argv[1]
    step_name = sys.argv[2]
    params_path = Path(sys.argv[3])

    db.init_db()  # idempotent; safe even though Streamlit already called it
    params = json.loads(params_path.read_text(encoding="utf-8"))

    func = STEP_FUNCS.get(step_name)
    if func is None:
        db.finish_step(job_id, step_name, "error", error=f"Unknown step: {step_name}")
        sys.exit(1)

    db.start_step(job_id, step_name)
    try:
        result = func(job_id, params)
        db.finish_step(job_id, step_name, "done", result=result)
    except Exception:
        db.finish_step(job_id, step_name, "error", error=traceback.format_exc())
        sys.exit(1)
    finally:
        try:
            params_path.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()