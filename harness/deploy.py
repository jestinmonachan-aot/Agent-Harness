"""Deployment step: ask Claude Code to dockerize the migrated app,
build + run it, and return the live URL."""

from __future__ import annotations

import subprocess
import re
from pathlib import Path

from harness.claude_cli import run_claude_prompt


def dockerize_and_run(output_repo_path: str, container_name: str, host_port: int = 8500) -> str:
    """
    Asks Claude Code to add Dockerfile/compose to the migrated app,
    then builds and runs the container. Returns the app URL.
    """
    prompt = (
        f"Add a production-ready Dockerfile (and docker-compose.yml if needed) "
        f"for the application at {output_repo_path}. The app should be runnable "
        f"with `docker build` and `docker run`, exposing its web port. "
        "Do not change core application logic — only add Docker config."
    )
    flat_prompt = " ".join(prompt.split("\n"))
    result = run_claude_prompt(flat_prompt, cwd=output_repo_path, timeout=900)
    if not result.success:
        raise RuntimeError(f"Dockerize prompt failed: {result.stderr}")

    # Check BEFORE attempting docker build
    dockerfile = Path(output_repo_path) / "Dockerfile"
    if not dockerfile.exists():
        raise RuntimeError(
            f"Claude Code did not create a Dockerfile. CLI stdout: {result.stdout[:500]}"
        )

    # Build image
    build = subprocess.run(
        ["docker", "build", "-t", container_name, "."],
        cwd=output_repo_path, capture_output=True, text=True,
    )
    if build.returncode != 0:
        raise RuntimeError(f"docker build failed: {build.stderr}")

    # Detect exposed port from Dockerfile
    exposed_port = 80
    match = re.search(r"EXPOSE\s+(\d+)", dockerfile.read_text())
    if match:
        exposed_port = int(match.group(1))

    # Run container, map host_port -> exposed_port
    run = subprocess.run(
        [
            "docker", "run", "-d",
            "--name", container_name,
            "-p", f"{host_port}:{exposed_port}",
            container_name,
        ],
        capture_output=True, text=True,
    )
    if run.returncode != 0:
        raise RuntimeError(f"docker run failed: {run.stderr}")

    return f"http://localhost:{host_port}"