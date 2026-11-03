from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from pathlib import Path

import pytest

from evidence_vault.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from evidence_vault.adapters.filesystem.atomic import ImmutableStoreConflict
from evidence_vault.application.archive_capture import ArchiveCapture
from evidence_vault.application.commands import ArchiveCaptureCommand, ArtifactPayload
from evidence_vault.domain.source import ArtifactRole, CaptureStatus


class NoFullReadStream(BytesIO):
    def read(self, size: int = -1) -> bytes:
        if size < 0:
            raise AssertionError("artifact must be read in bounded chunks")
        return super().read(size)


def payload(data: bytes = b"raw video", *, guarded: bool = False) -> ArtifactPayload:
    stream = NoFullReadStream(data) if guarded else BytesIO(data)
    return ArtifactPayload(
        role=ArtifactRole.ORIGINAL_VIDEO,
        media_type="video/mp4",
        stream=stream,
        original_filename="video.mp4",
    )


def command(source, captured_at, collector, data: bytes = b"raw video", **kwargs):
    return ArchiveCaptureCommand(
        source=source,
        captured_at=captured_at,
        collector=collector,
        artifacts=(payload(data, guarded=kwargs.pop("guarded", False)),),
        **kwargs,
    )


def build_use_case(root: Path) -> ArchiveCapture:
    return ArchiveCapture(
        artifacts=FileSystemArtifactStore(root),
        packets=FileSystemSourcePacketRepository(root),
    )


def test_identical_capture_is_idempotent_and_deduplicated(
    tmp_path: Path,
    source,
    captured_at,
    collector,
) -> None:
    use_case = build_use_case(tmp_path)

    first = use_case.handle(command(source, captured_at, collector, guarded=True))
    second = use_case.handle(command(source, captured_at, collector, guarded=True))

    assert first.created is True
    assert second.created is False
    assert first.packet == second.packet
    artifact_files = [path for path in (tmp_path / "artifacts").rglob("*") if path.is_file()]
    packet_files = list((tmp_path / "sources").rglob("*.json"))
    assert len(artifact_files) == 1
    assert len(packet_files) == 1


def test_recapture_preserves_history_and_shares_identical_bytes(
    tmp_path: Path,
    source,
    captured_at,
    collector,
) -> None:
    use_case = build_use_case(tmp_path)
    first = use_case.handle(command(source, captured_at, collector))
    second = use_case.handle(command(source, captured_at + timedelta(minutes=1), collector))

    assert first.packet.source_id == second.packet.source_id
    assert first.packet.capture_id != second.packet.capture_id
    assert first.packet.artifacts[0].artifact_id == second.packet.artifacts[0].artifact_id
    assert len(list((tmp_path / "sources").rglob("*.json"))) == 2
    artifact_files = [path for path in (tmp_path / "artifacts").rglob("*") if path.is_file()]
    assert len(artifact_files) == 1


def test_artifact_bytes_can_be_read_back(
    tmp_path: Path,
    source,
    captured_at,
    collector,
) -> None:
    store = FileSystemArtifactStore(tmp_path)
    use_case = ArchiveCapture(
        artifacts=store,
        packets=FileSystemSourcePacketRepository(tmp_path),
    )
    result = use_case.handle(command(source, captured_at, collector, b"some video bytes"))

    with store.open(result.packet.artifacts[0].artifact_id) as stream:
        assert stream.read() == b"some video bytes"


def test_partial_capture_keeps_explicit_limitations(
    tmp_path: Path,
    source,
    captured_at,
    collector,
) -> None:
    result = build_use_case(tmp_path).handle(
        command(
            source,
            captured_at,
            collector,
            status=CaptureStatus.PARTIAL,
            limitations=("audio track unavailable",),
        )
    )

    assert result.packet.status == CaptureStatus.PARTIAL
    assert result.packet.limitations == ("audio track unavailable",)


def test_empty_artifact_is_rejected_without_a_source_packet(
    tmp_path: Path,
    source,
    captured_at,
    collector,
) -> None:
    with pytest.raises(ValueError, match="empty artifacts"):
        build_use_case(tmp_path).handle(command(source, captured_at, collector, b""))

    assert not (tmp_path / "sources").exists()


def test_use_case_rejects_capture_without_artifacts(
    tmp_path: Path,
    source,
    captured_at,
    collector,
) -> None:
    invalid = ArchiveCaptureCommand(
        source=source,
        captured_at=captured_at,
        collector=collector,
        artifacts=(),
    )

    with pytest.raises(ValueError, match="at least one raw artifact"):
        build_use_case(tmp_path).handle(invalid)


def test_artifact_verification_detects_disk_corruption(
    tmp_path: Path,
    source,
    captured_at,
    collector,
) -> None:
    store = FileSystemArtifactStore(tmp_path)
    use_case = ArchiveCapture(
        artifacts=store,
        packets=FileSystemSourcePacketRepository(tmp_path),
    )
    result = use_case.handle(command(source, captured_at, collector))
    artifact_id = result.packet.artifacts[0].artifact_id
    digest = artifact_id.removeprefix("art_")
    artifact_path = tmp_path / "artifacts" / digest[:2] / digest

    assert store.verify(artifact_id) is True
    artifact_path.write_bytes(b"corrupted")
    assert store.verify(artifact_id) is False


def test_packet_repository_revalidates_nested_metadata_before_write(
    tmp_path: Path,
    source,
    captured_at,
    collector,
) -> None:
    repository = FileSystemSourcePacketRepository(tmp_path)
    result = build_use_case(tmp_path).handle(command(source, captured_at, collector))
    packet = result.packet
    packet.metadata["unexpected"] = "mutation after validation"

    with pytest.raises(ValueError, match="capture_id does not match"):
        repository.save(packet)


def test_existing_corrupt_packet_is_never_overwritten(
    tmp_path: Path,
    source,
    captured_at,
    collector,
) -> None:
    repository = FileSystemSourcePacketRepository(tmp_path)
    result = build_use_case(tmp_path).handle(command(source, captured_at, collector))
    packet = result.packet
    packet_path = tmp_path / "sources" / packet.source_id / f"{packet.capture_id}.json"
    packet_path.write_text("corrupted", encoding="utf-8")

    with pytest.raises(ImmutableStoreConflict):
        repository.save(packet)
    assert packet_path.read_text(encoding="utf-8") == "corrupted"
