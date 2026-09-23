"""Deployment step: ask Claude Code to dockerize the migrated app,
build + run it, and return the live URL."""

from __future__ import annotations

import subprocess
import re
from pathlib import Path

from harness.claude_cli import run_claude_prompt


def _find_dockerfiles(root: Path) -> dict:
    """Look for the layouts we know how to handle."""
    return {
        "root_dockerfile": root / "Dockerfile",
        "compose": root / "docker-compose.yml" if (root / "docker-compose.yml").exists()
                   else (root / "docker-compose.yaml" if (root / "docker-compose.yaml").exists() else None),
        "backend_dockerfile": root / "backend" / "Dockerfile",
        "frontend_dockerfile": root / "frontend" / "Dockerfile",
    }


def _generate_compose(root: Path, backend_port: int = 8000, frontend_port: int = 80) -> Path:
    """Minimal compose file for a backend/ + frontend/ split, when Claude
    created per-service Dockerfiles but no compose to wire them together."""
    compose_content = f"""services:
  backend:
    build: ./backend
    ports:
      - "{backend_port}:{backend_port}"
    restart: unless-stopped
  frontend:
    build: ./frontend
    ports:
      - "{frontend_port}:80"
    depends_on:
      - backend
    restart: unless-stopped
"""
    compose_path = root / "docker-compose.generated.yml"
    compose_path.write_text(compose_content, encoding="utf-8")
    return compose_path


def dockerize_and_run(output_repo_path: str, container_name: str, host_port: int = 8500) -> str:
    """
    Asks Claude Code to add Dockerfile/compose to the migrated app,
    then builds and runs it. Returns the app URL.
    """
    root = Path(output_repo_path)

    prompt = (
        f"Add a production-ready Dockerfile (and docker-compose.yml if needed) "
        f"for the application at {output_repo_path}. The app should be runnable "
        f"with `docker build` and `docker run`, exposing its web port. "
        "Do not change core application logic — only add Docker config."
    )
    flat_prompt = " ".join(prompt.split("\n"))
    result = run_claude_prompt(flat_prompt, cwd=output_repo_path, timeout=900)
    if not result.success:
        raise RuntimeError(
            f"Dockerize prompt failed. returncode={result.returncode}, "
            f"stdout={result.stdout[:500]!r}, stderr={result.stderr[:500]!r}"
        )

    layout = _find_dockerfiles(root)

    if layout["compose"] is not None:
        compose_file = layout["compose"]
        up = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "up", "-d", "--build"],
            cwd=str(root), capture_output=True, text=True,
        )
        if up.returncode != 0:
            raise RuntimeError(f"docker compose up failed: {up.stderr}")
        return f"http://localhost:{host_port}"

    if layout["root_dockerfile"].exists():
        dockerfile = layout["root_dockerfile"]
        build = subprocess.run(
            ["docker", "build", "-t", container_name, "."],
            cwd=str(root), capture_output=True, text=True,
        )
        if build.returncode != 0:
            raise RuntimeError(f"docker build failed: {build.stderr}")

        exposed_port = 80
        match = re.search(r"EXPOSE\s+(\d+)", dockerfile.read_text())
        if match:
            exposed_port = int(match.group(1))

        run = subprocess.run(
            ["docker", "run", "-d", "--name", container_name,
             "-p", f"{host_port}:{exposed_port}", container_name],
            capture_output=True, text=True,
        )
        if run.returncode != 0:
            raise RuntimeError(f"docker run failed: {run.stderr}")
        return f"http://localhost:{host_port}"

    if layout["backend_dockerfile"].exists() and layout["frontend_dockerfile"].exists():
        backend_port = 8000
        match = re.search(r"EXPOSE\s+(\d+)", layout["backend_dockerfile"].read_text())
        if match:
            backend_port = int(match.group(1))

        compose_path = _generate_compose(root, backend_port=backend_port)
        up = subprocess.run(
            ["docker", "compose", "-f", str(compose_path), "-p", container_name, "up", "-d", "--build"],
            cwd=str(root), capture_output=True, text=True,
        )
        if up.returncode != 0:
            raise RuntimeError(f"docker compose up failed (generated compose): {up.stderr}")
        return f"http://localhost:{host_port} (frontend), http://localhost:{backend_port} (backend api)"

    raise RuntimeError(
        f"Claude Code did not create a usable Docker layout (checked root Dockerfile, "
        f"docker-compose.yml, and backend/+frontend/ Dockerfiles). "
        f"CLI stdout: {result.stdout[:500]}"
    )