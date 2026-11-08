from __future__ import annotations

import yaml

from evidence_vault.application.projection_ports import ProjectionStore
from evidence_vault.application.projection_types import GeneratedDocument


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
        properties = {
            "schema_version": "triage-dashboard/v1",
            "generated": True,
            "generator": "evidence-vault",
            "generator_version": "1",
            "status": status,
        }
        frontmatter = yaml.safe_dump(properties, allow_unicode=True, sort_keys=False).strip()
        lines = [
            "---",
            frontmatter,
            "---",
            "",
            f"# {status.capitalize()}",
            "",
            "> [!note] Generated view",
            "> Requires the Dataview plugin. Rebuildable; edit human notes in `ideas/`.",
            "",
            "```dataview",
            f'TABLE title, collections FROM "ideas" WHERE status = "{status}"',
            "```",
            "",
        ]
        return GeneratedDocument(path=f"{status}.md", content="\n".join(lines))
