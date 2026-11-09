from __future__ import annotations

from sourcefolio.application.markdown import generated_callout, generated_frontmatter
from sourcefolio.application.projection_ports import ProjectionStore
from sourcefolio.application.projection_types import GeneratedDocument


class TriageDashboardProjection:
    NAMESPACE = "dashboards"
    STATUSES = ("inbox", "keep", "drop", "done")

    def __init__(self, *, store: ProjectionStore) -> None:
        self._store = store

    def rebuild(self) -> int:
        return self._store.replace_namespace(
            self.NAMESPACE,
            (self._render(status) for status in self.STATUSES),
        )

    @staticmethod
    def _render(status: str) -> GeneratedDocument:
        frontmatter = generated_frontmatter(
            {"status": status},
            schema_version="triage-dashboard/v1",
            generator_version="1",
        )
        lines = [
            "---",
            frontmatter,
            "---",
            "",
            f"# {status.capitalize()}",
            "",
            *generated_callout(
                "Requires the Dataview plugin. Rebuildable; edit human notes in `ideas/`."
            ),
            "",
            "```dataview",
            f'TABLE title, collections FROM "ideas" WHERE status = "{status}"',
            "```",
            "",
        ]
        return GeneratedDocument(path=f"{status}.md", content="\n".join(lines))
