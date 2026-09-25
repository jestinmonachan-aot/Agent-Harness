"""Deployment step: package the migrated app into a container and run
it, returning the live URL. User-facing language avoids naming the
underlying tooling (container engine, orchestration) since that's an
implementation detail that may change."""

from __future__ import annotations

import subprocess
import re
from pathlib import Path

from harness.claude_cli import run_claude_prompt


def _find_dockerfiles(root: Path) -> dict:
    return {
        "root_dockerfile": root / "Dockerfile",
        "compose": root / "docker-compose.yml" if (root / "docker-compose.yml").exists()
                   else (root / "docker-compose.yaml" if (root / "docker-compose.yaml").exists() else None),
        "backend_dockerfile": root / "backend" / "Dockerfile",
        "frontend_dockerfile": root / "frontend" / "Dockerfile",
    }


def _generate_compose(root: Path, backend_port: int = 8000, frontend_port: int = 80,
                       host_frontend_port: int = 8500) -> Path:
    compose_content = f"""services:
  backend:
    build: ./backend
    ports:
      - "{backend_port}:{backend_port}"
    restart: unless-stopped
  frontend:
    build: ./frontend
    ports:
      - "{host_frontend_port}:{frontend_port}"
    depends_on:
      - backend
    restart: unless-stopped
"""
    compose_path = root / "docker-compose.generated.yml"
    compose_path.write_text(compose_content, encoding="utf-8")
    return compose_path


def _extract_host_port(compose_path: Path, fallback: int) -> int:
    try:
        text = compose_path.read_text(encoding="utf-8")
    except OSError:
        return fallback

    frontend_block_match = re.search(
        r"^\s*frontend:\s*\n(.*?)(?=^\s{0,2}\S+:\s*$|\Z)",
        text, re.MULTILINE | re.DOTALL,
    )
    block = frontend_block_match.group(1) if frontend_block_match else text

    port_match = re.search(r'["\']?(\d{2,5}):(\d{2,5})["\']?', block)
    if port_match:
        return int(port_match.group(1))
    return fallback


def _port_in_use(port: int) -> bool:
    try:
        check = subprocess.run(
            ["docker", "ps", "--format", "{{.Ports}}"],
            capture_output=True, text=True,
        )
        return f"0.0.0.0:{port}->" in check.stdout or f":{port}->" in check.stdout
    except OSError:
        return False


def deploy_app(output_repo_path: str, container_name: str, host_port: int = 8500) -> str:
    """Packages and runs the migrated app. Returns the live app URL.
    (Formerly `dockerize_and_run` - renamed since the name is now
    user-facing terminology via app.py; behavior is unchanged.)"""
    root = Path(output_repo_path)

    if _port_in_use(host_port):
        raise RuntimeError(
            f"Port {host_port} is already in use by a running deployment. "
            f"Stop it first or choose a different port."
        )

    prompt = (
        f"Add a production-ready Dockerfile (and docker-compose.yml if needed) "
        f"for the application at {output_repo_path}. The app should be runnable "
        f"with `docker build` and `docker run`, exposing its web port. "
        f"IMPORTANT: the frontend/web service's host-side port mapping in any "
        f"docker-compose.yml MUST be exactly {host_port} (e.g. \"{host_port}:80\" or "
        f"\"{host_port}:<container_port>\") — do not use port 80, 3000, or any other "
        f"default on the host side, only {host_port}. "
        "Do not change core application logic — only add Docker config."
    )
    flat_prompt = " ".join(prompt.split("\n"))
    result = run_claude_prompt(flat_prompt, cwd=output_repo_path, timeout=900)
    if not result.success:
        raise RuntimeError(
            f"Deployment packaging failed. returncode={result.returncode}, "
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
            raise RuntimeError(f"Deployment failed to start: {up.stderr}")
        actual_port = _extract_host_port(compose_file, fallback=host_port)
        return f"http://localhost:{actual_port}"

    if layout["root_dockerfile"].exists():
        dockerfile = layout["root_dockerfile"]
        build = subprocess.run(
            ["docker", "build", "-t", container_name, "."],
            cwd=str(root), capture_output=True, text=True,
        )
        if build.returncode != 0:
            raise RuntimeError(f"Deployment build failed: {build.stderr}")

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
            raise RuntimeError(f"Deployment failed to start: {run.stderr}")
        return f"http://localhost:{host_port}"

    if layout["backend_dockerfile"].exists() and layout["frontend_dockerfile"].exists():
        backend_port = 8000
        match = re.search(r"EXPOSE\s+(\d+)", layout["backend_dockerfile"].read_text())
        if match:
            backend_port = int(match.group(1))

        frontend_port = 80
        match = re.search(r"EXPOSE\s+(\d+)", layout["frontend_dockerfile"].read_text())
        if match:
            frontend_port = int(match.group(1))

        compose_path = _generate_compose(
            root, backend_port=backend_port,
            frontend_port=frontend_port, host_frontend_port=host_port,
        )
        up = subprocess.run(
            ["docker", "compose", "-f", str(compose_path), "-p", container_name, "up", "-d", "--build"],
            cwd=str(root), capture_output=True, text=True,
        )
        if up.returncode != 0:
            raise RuntimeError(f"Deployment failed to start (generated config): {up.stderr}")
        return f"http://localhost:{host_port} (frontend), http://localhost:{backend_port} (backend api)"

    raise RuntimeError(
        "The migrated app has no recognizable deployment layout (checked for a "
        "root Dockerfile, docker-compose file, and backend/+frontend/ Dockerfiles)."
    )


# Backward-compatible alias — worker.py imports this name.
dockerize_and_run = deploy_app