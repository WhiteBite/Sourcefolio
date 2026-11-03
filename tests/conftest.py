from __future__ import annotations

import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evidence_vault.domain.common import ActorKind, Provenance
from evidence_vault.domain.source import (
    ArtifactRef,
    ArtifactRole,
    SourceIdentity,
    SourceKind,
)


def write_fake_executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        script = path.with_name(path.name + ".py")
        script.write_text(body, encoding="utf-8")
        wrapper = path.with_name(path.name + ".cmd")
        wrapper.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
        return wrapper
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def source() -> SourceIdentity:
    return SourceIdentity(
        platform="tiktok",
        kind=SourceKind.VIDEO,
        external_id="video-0001",
        canonical_url="https://www.tiktok.com/@example/video/video-0001",
    )


@pytest.fixture
def collector() -> Provenance:
    return Provenance(
        actor_kind=ActorKind.TOOL,
        actor_id="test-collector",
        method="pytest",
        version="1",
    )


@pytest.fixture
def captured_at() -> datetime:
    return datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


@pytest.fixture
def artifact_ref():
    def build(
        data: bytes = b"raw video",
        role: ArtifactRole = ArtifactRole.ORIGINAL_VIDEO,
    ) -> ArtifactRef:
        digest = hashlib.sha256(data).hexdigest()
        return ArtifactRef(
            artifact_id=f"art_{digest}",
            role=role,
            media_type="application/octet-stream",
            size_bytes=len(data),
            sha256=digest,
        )

    return build
