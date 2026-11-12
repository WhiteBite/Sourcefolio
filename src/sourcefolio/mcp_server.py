from __future__ import annotations

import io
import threading
from contextlib import redirect_stderr, redirect_stdout

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from sourcefolio import cli

server = MCPServer("sourcefolio")

_CAPTURE_LOCK = threading.Lock()


def _annotations(title: str, *, read_only: bool, open_world: bool = False) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        read_only_hint=read_only,
        destructive_hint=False,
        open_world_hint=open_world,
    )


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


@server.tool(annotations=_annotations("Search archive", read_only=True))
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


@server.tool(annotations=_annotations("Build evidence pack", read_only=True))
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


@server.tool(annotations=_annotations("Import collections", read_only=False))
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


@server.tool(annotations=_annotations("Fetch slides", read_only=False))
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


@server.tool(annotations=_annotations("Project Obsidian vault", read_only=False))
def project_obsidian(
    archive_dir: str = "data/archive",
    derived_dir: str = "data/derived",
    vault: str = "data/vault",
    scaffold_ideas: bool = False,
    projects: str = "data/projects.json",
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
        "--projects",
        projects,
    ]
    if scaffold_ideas:
        arguments.append("--scaffold-ideas")
    return _run(arguments)


@server.tool(annotations=_annotations("Scaffold ideas", read_only=False))
def scaffold_ideas(archive_dir: str = "data/archive", vault: str = "data/vault") -> str:
    """Create missing human idea notes under <vault>/ideas without overwriting."""
    return _run(["scaffold-ideas", "--archive-dir", archive_dir, "--vault", vault])


@server.tool(annotations=_annotations("Enrich with LLM", read_only=False, open_world=True))
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


@server.tool(annotations=_annotations("Build LLM catalog", read_only=False, open_world=True))
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


@server.tool(annotations=_annotations("Catalog repositories", read_only=False, open_world=True))
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


@server.tool(annotations=_annotations("Match projects", read_only=False))
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


@server.tool(annotations=_annotations("Extract registry mentions", read_only=False))
def registry_extract(
    projects: str = "data/projects.json",
    profiles: str = "data/vault/projects",
    archive_dir: str = "data/archive",
    derived_dir: str = "data/derived",
    vault: str = "data/vault",
    project: list[str] | None = None,
    collection: list[str] | None = None,
    limit: int | None = None,
    max_ocr_kb: int = 20,
    max_caption_kb: int = 20,
    refresh: bool = False,
    dry_run: bool = False,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> str:
    """Extract anchored candidate mentions from saved items for each project."""
    arguments = [
        "registry-extract",
        "--projects",
        projects,
        "--profiles",
        profiles,
        "--archive-dir",
        archive_dir,
        "--derived-dir",
        derived_dir,
        "--vault",
        vault,
        "--max-ocr-kb",
        str(max_ocr_kb),
        "--max-caption-kb",
        str(max_caption_kb),
    ]
    for name in project or []:
        arguments += ["--project", name]
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


@server.tool(annotations=_annotations("Build registry snapshot", read_only=False))
def registry_build(
    projects: str = "data/projects.json",
    profiles: str = "data/vault/projects",
    derived_dir: str = "data/derived",
    vault: str = "data/vault",
    dry_run: bool = False,
) -> str:
    """Aggregate extracted routes into a registry candidate snapshot."""
    arguments = [
        "registry-build",
        "--projects",
        projects,
        "--profiles",
        profiles,
        "--derived-dir",
        derived_dir,
        "--vault",
        vault,
    ]
    if dry_run:
        arguments.append("--dry-run")
    return _run(arguments)


@server.tool(annotations=_annotations("Decide candidate", read_only=False))
def candidate_decide(
    candidate_id: str,
    status: str = "inbox",
    rationale: str | None = None,
    approved_by: str | None = None,
    merge_into: str | None = None,
    projects: str = "data/projects.json",
    derived_dir: str = "data/derived",
    vault: str = "data/vault",
) -> str:
    """Record a human decision on one registry candidate."""
    arguments = [
        "candidate-decide",
        "--candidate-id",
        candidate_id,
        "--status",
        status,
        "--projects",
        projects,
        "--derived-dir",
        derived_dir,
        "--vault",
        vault,
    ]
    if rationale is not None:
        arguments += ["--rationale", rationale]
    if approved_by is not None:
        arguments += ["--approved-by", approved_by]
    if merge_into is not None:
        arguments += ["--merge-into", merge_into]
    return _run(arguments)


@server.tool(annotations=_annotations("Audit archive", read_only=True))
def archive_audit(archive_dir: str = "data/archive", verify_bytes: bool = False) -> str:
    """Verify artifact references and optionally hash all archived bytes."""
    arguments = ["archive-audit", "--archive-dir", archive_dir]
    if verify_bytes:
        arguments.append("--verify-bytes")
    return _run(arguments)


@server.tool(annotations=_annotations("Capture report", read_only=True))
def capture_report(
    control_db: str = "data/control/capture.sqlite3",
    format: str = "json",
) -> str:
    """Report capture status, retries, durations, and recent failures."""
    return _run(["capture-report", "--control-db", control_db, "--format", format])


@server.tool(annotations=_annotations("Export schema", read_only=True))
def schema(contract: str) -> str:
    """Export a versioned JSON Schema for a contract."""
    return _run(["schema", contract])


@server.tool(annotations=_annotations("Build context brief", read_only=False))
def context(
    vault: str = "data/vault",
    derived_dir: str = "data/derived",
    archive_dir: str = "data/archive",
    projects: str = "data/projects.json",
    format: str = "json",
) -> str:
    """Build a session context brief from projects, matches, and decisions."""
    return _run(
        [
            "context",
            "--vault",
            vault,
            "--derived-dir",
            derived_dir,
            "--archive-dir",
            archive_dir,
            "--projects",
            projects,
            "--format",
            format,
        ]
    )


@server.tool(annotations=_annotations("Scaffold projects", read_only=False))
def scaffold_projects(vault: str = "data/vault", projects: str = "data/projects.json") -> str:
    """Create missing human project profile notes under <vault>/projects."""
    return _run(["scaffold-projects", "--vault", vault, "--projects", projects])


@server.tool(annotations=_annotations("Create decision", read_only=False))
def decision_new(
    title: str,
    vault: str = "data/vault",
    problem: str = "",
    options: list[str] | None = None,
    chosen: str = "",
    rationale: str = "",
    follow_ups: list[str] | None = None,
    project_ids: list[str] | None = None,
    tags: list[str] | None = None,
    date: str = "",
) -> str:
    """Create a human decision note under <vault>/decisions without overwriting."""
    if not title.strip():
        return "error: title is required"
    arguments = [
        "decision-new",
        "--vault",
        vault,
        "--title",
        title,
        "--problem",
        problem,
        "--chosen",
        chosen,
        "--rationale",
        rationale,
    ]
    for option in options or []:
        arguments += ["--option", option]
    for follow_up in follow_ups or []:
        arguments += ["--follow-up", follow_up]
    for project in project_ids or []:
        arguments += ["--project", project]
    for tag in tags or []:
        arguments += ["--tag", tag]
    if date:
        arguments += ["--date", date]
    return _run(arguments)


@server.tool(annotations=_annotations("Search decisions", read_only=True))
def decision_search(query: str, vault: str = "data/vault", limit: int = 20) -> str:
    """Search human decision notes by terms."""
    return _run(["decision-search", query, "--vault", vault, "--limit", str(limit)])


@server.tool(annotations=_annotations("Ask archive", read_only=False, open_world=True))
def ask(
    query: str,
    project: str | None = None,
    no_llm: bool = False,
    format: str = "json",
    limit: int = 10,
    excerpt_limit: int = 3,
    archive_dir: str = "data/archive",
    derived_dir: str = "data/derived",
    vault: str = "data/vault",
    collection: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    projects: str = "data/projects.json",
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> str:
    """Answer a question from retrieved evidence, optionally with a grounded LLM synthesis."""
    arguments = [
        "ask",
        query,
        "--format",
        format,
        "--limit",
        str(limit),
        "--excerpt-limit",
        str(excerpt_limit),
        "--archive-dir",
        archive_dir,
        "--derived-dir",
        derived_dir,
        "--vault",
        vault,
        "--projects",
        projects,
    ]
    if project is not None:
        arguments += ["--project", project]
    if no_llm:
        arguments.append("--no-llm")
    if collection is not None:
        arguments += ["--collection", collection]
    if kind is not None:
        arguments += ["--kind", kind]
    if status is not None:
        arguments += ["--status", status]
    if base_url is not None:
        arguments += ["--base-url", base_url]
    if api_key is not None:
        arguments += ["--api-key", api_key]
    if model is not None:
        arguments += ["--model", model]
    return _run(arguments)


@server.tool(annotations=_annotations("Draft project profile", read_only=False))
def profile_draft(
    projects: str = "data/projects.json",
    vault: str = "data/vault",
    repo: str | None = None,
    id: str | None = None,
) -> str:
    """Draft human project profiles from each project's own repository."""
    arguments = ["profile-draft", "--projects", projects, "--vault", vault]
    if repo is not None:
        arguments += ["--repo", repo]
    if id is not None:
        arguments += ["--id", id]
    return _run(arguments)


def main() -> None:
    server.run()
