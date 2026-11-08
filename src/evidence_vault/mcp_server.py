from __future__ import annotations

import io
import threading
from contextlib import redirect_stderr, redirect_stdout

from mcp.server.mcpserver import MCPServer

from evidence_vault import cli

server = MCPServer("evidence-vault")

_CAPTURE_LOCK = threading.Lock()


def _run(arguments: list[str]) -> str:
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with _CAPTURE_LOCK, redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(arguments)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    except Exception as exc:
        return f"error: {exc}"
    output = stdout.getvalue()
    if code == 0:
        return output
    detail = stderr.getvalue().strip() or output.strip()
    return f"error (exit {code}): {detail}"


@server.tool()
def search(
    query: str,
    collection: str | None = None,
    limit: int = 10,
    archive_dir: str = "data/archive",
    textual_limit: int | None = None,
) -> str:
    """Search archived source metadata and extracted text."""
    arguments = ["search", query, "--archive-dir", archive_dir, "--limit", str(limit)]
    if collection is not None:
        arguments += ["--collection", collection]
    if textual_limit is not None:
        arguments += ["--max-text-kb", str(textual_limit)]
    return _run(arguments)


@server.tool()
def evidence_pack(
    query: str,
    format: str = "json",
    excerpt_limit: int = 3,
    limit: int = 10,
    archive_dir: str = "data/archive",
    derived_dir: str = "data/derived",
) -> str:
    """Build a retrieval-only JSON or Markdown evidence pack."""
    return _run(
        [
            "evidence-pack",
            query,
            "--archive-dir",
            archive_dir,
            "--derived-dir",
            derived_dir,
            "--format",
            format,
            "--limit",
            str(limit),
            "--excerpt-limit",
            str(excerpt_limit),
        ]
    )


@server.tool()
def import_collections(
    file: str,
    archive_dir: str = "data/archive",
    control_db: str = "data/control/capture.sqlite3",
    concurrency: int = 4,
    limit: int | None = None,
) -> str:
    """Archive collection-capture/v1 metadata with resumable bounded workers."""
    arguments = [
        "import-collections",
        file,
        "--archive-dir",
        archive_dir,
        "--control-db",
        control_db,
        "--concurrency",
        str(concurrency),
    ]
    if limit is not None:
        arguments += ["--limit", str(limit)]
    return _run(arguments)


@server.tool()
def fetch_slides(
    collection: str,
    archive_dir: str = "data/archive",
    control_db: str = "data/control/capture.sqlite3",
    limit: int | None = None,
    max_slides: int = 50,
    max_mb: int = 20,
) -> str:
    """Download slideshow images for a collection and archive them as image artifacts."""
    arguments = [
        "fetch-slides",
        "--collection",
        collection,
        "--archive-dir",
        archive_dir,
        "--control-db",
        control_db,
        "--max-slides",
        str(max_slides),
        "--max-mb",
        str(max_mb),
    ]
    if limit is not None:
        arguments += ["--limit", str(limit)]
    return _run(arguments)


@server.tool()
def project_obsidian(
    archive_dir: str = "data/archive",
    derived_dir: str = "data/derived",
    vault: str = "data/vault",
    scaffold_ideas: bool = False,
) -> str:
    """Rebuild generated Obsidian source cards from canonical packets."""
    arguments = [
        "project-obsidian",
        "--archive-dir",
        archive_dir,
        "--derived-dir",
        derived_dir,
        "--vault",
        vault,
    ]
    if scaffold_ideas:
        arguments.append("--scaffold-ideas")
    return _run(arguments)


@server.tool()
def scaffold_ideas(archive_dir: str = "data/archive", vault: str = "data/vault") -> str:
    """Create missing human idea notes under <vault>/ideas without overwriting."""
    return _run(["scaffold-ideas", "--archive-dir", archive_dir, "--vault", vault])


@server.tool()
def llm_enrich(
    collection: str,
    archive_dir: str = "data/archive",
    derived_dir: str = "data/derived",
    vault: str = "data/vault",
    limit: int | None = None,
    refresh: bool = False,
    dry_run: bool = False,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> str:
    """Enrich a collection's latest captures with an OpenAI-compatible chat model."""
    arguments = [
        "llm-enrich",
        "--collection",
        collection,
        "--archive-dir",
        archive_dir,
        "--derived-dir",
        derived_dir,
        "--vault",
        vault,
    ]
    if limit is not None:
        arguments += ["--limit", str(limit)]
    if refresh:
        arguments.append("--refresh")
    if dry_run:
        arguments.append("--dry-run")
    if base_url is not None:
        arguments += ["--base-url", base_url]
    if api_key is not None:
        arguments += ["--api-key", api_key]
    if model is not None:
        arguments += ["--model", model]
    return _run(arguments)


@server.tool()
def llm_catalog(
    collection: str,
    archive_dir: str = "data/archive",
    derived_dir: str = "data/derived",
    vault: str = "data/vault",
    limit: int | None = None,
    refresh: bool = False,
    dry_run: bool = False,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> str:
    """Build a deduplicated catalog of works from a collection's latest captures."""
    arguments = [
        "llm-catalog",
        "--collection",
        collection,
        "--archive-dir",
        archive_dir,
        "--derived-dir",
        derived_dir,
        "--vault",
        vault,
    ]
    if limit is not None:
        arguments += ["--limit", str(limit)]
    if refresh:
        arguments.append("--refresh")
    if dry_run:
        arguments.append("--dry-run")
    if base_url is not None:
        arguments += ["--base-url", base_url]
    if api_key is not None:
        arguments += ["--api-key", api_key]
    if model is not None:
        arguments += ["--model", model]
    return _run(arguments)


@server.tool()
def repo_catalog(
    collection: str,
    archive_dir: str = "data/archive",
    derived_dir: str = "data/derived",
    vault: str = "data/vault",
    limit: int | None = None,
    refresh: bool = False,
    dry_run: bool = False,
    token: str | None = None,
) -> str:
    """Build a live GitHub repository catalog for one collection."""
    arguments = [
        "repo-catalog",
        "--collection",
        collection,
        "--archive-dir",
        archive_dir,
        "--derived-dir",
        derived_dir,
        "--vault",
        vault,
    ]
    if limit is not None:
        arguments += ["--limit", str(limit)]
    if refresh:
        arguments.append("--refresh")
    if dry_run:
        arguments.append("--dry-run")
    if token is not None:
        arguments += ["--token", token]
    return _run(arguments)


@server.tool()
def project_fit(
    collection: list[str] | None = None,
    projects: str = "data/projects.json",
    archive_dir: str = "data/archive",
    derived_dir: str = "data/derived",
    vault: str = "data/vault",
    limit: int | None = None,
    max_ocr_kb: int = 20,
    refresh: bool = False,
    dry_run: bool = False,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> str:
    """Match saved items to the user's projects and write per-project briefs."""
    arguments = [
        "project-fit",
        "--projects",
        projects,
        "--archive-dir",
        archive_dir,
        "--derived-dir",
        derived_dir,
        "--vault",
        vault,
        "--max-ocr-kb",
        str(max_ocr_kb),
    ]
    for name in collection or []:
        arguments += ["--collection", name]
    if limit is not None:
        arguments += ["--limit", str(limit)]
    if refresh:
        arguments.append("--refresh")
    if dry_run:
        arguments.append("--dry-run")
    if base_url is not None:
        arguments += ["--base-url", base_url]
    if api_key is not None:
        arguments += ["--api-key", api_key]
    if model is not None:
        arguments += ["--model", model]
    return _run(arguments)


@server.tool()
def archive_audit(archive_dir: str = "data/archive", verify_bytes: bool = False) -> str:
    """Verify artifact references and optionally hash all archived bytes."""
    arguments = ["archive-audit", "--archive-dir", archive_dir]
    if verify_bytes:
        arguments.append("--verify-bytes")
    return _run(arguments)


@server.tool()
def capture_report(
    control_db: str = "data/control/capture.sqlite3",
    format: str = "json",
) -> str:
    """Report capture status, retries, durations, and recent failures."""
    return _run(["capture-report", "--control-db", control_db, "--format", format])


@server.tool()
def schema(contract: str) -> str:
    """Export a versioned JSON Schema for a contract."""
    return _run(["schema", contract])


def main() -> None:
    server.run()
