from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from conftest import write_fake_executable
from evidence_vault.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from evidence_vault.application.archive_capture import ArchiveCapture
from evidence_vault.application.commands import ArchiveCaptureCommand, ArtifactPayload
from evidence_vault.application.ocr_backlog_report import OcrBacklogReportDocument
from evidence_vault.application.ocr_evaluation import OcrEvaluationReport, OcrEvaluationSet
from evidence_vault.cli import main
from evidence_vault.domain.common import ActorKind, Provenance
from evidence_vault.domain.source import ArtifactRole, SourceIdentity, SourceKind


def create_capture(
    archive: Path,
    external_id: str = "slides",
    images: tuple[bytes, ...] = (b"image",),
) -> str:
    packet = (
        ArchiveCapture(
            artifacts=FileSystemArtifactStore(archive),
            packets=FileSystemSourcePacketRepository(archive),
        )
        .handle(
            ArchiveCaptureCommand(
                source=SourceIdentity(
                    platform="test",
                    kind=SourceKind.SLIDESHOW,
                    external_id=external_id,
                ),
                captured_at=datetime(2026, 11, 3, 12, 0, tzinfo=UTC),
                collector=Provenance(
                    actor_kind=ActorKind.TOOL,
                    actor_id="test",
                    method="test",
                ),
                artifacts=tuple(
                    ArtifactPayload(
                        role=ArtifactRole.ORIGINAL_IMAGE,
                        media_type="image/jpeg",
                        stream=BytesIO(image),
                        original_filename=f"slide-{index:02d}.jpg",
                    )
                    for index, image in enumerate(images, start=1)
                ),
            )
        )
        .packet
    )
    return packet.capture_id


def create_image_capture(archive: Path) -> str:
    return create_capture(archive)


def fake_tesseract(path: Path) -> Path:
    return write_fake_executable(
        path,
        """import sys
from pathlib import Path
if "--version" in sys.argv:
    print("fake-tesseract-cli")
    raise SystemExit(0)
input_path = Path(sys.argv[sys.argv.index("stdout") - 1])
if b"bad" in input_path.read_bytes():
    print("bad synthetic image", file=sys.stderr)
    raise SystemExit(2)
print("derived-search-needle")
""",
    )


def test_ocr_cli_is_resumable_and_updates_only_derived_search(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    capture_id = create_image_capture(archive)
    packet_path = next((archive / "sources").rglob(f"{capture_id}.json"))
    raw_before = packet_path.read_bytes()
    executable = fake_tesseract(tmp_path / "fake-tesseract")
    arguments = [
        "ocr-capture",
        capture_id,
        "--archive-dir",
        str(archive),
        "--derived-dir",
        str(derived),
        "--executable",
        str(executable),
        "--temp-dir",
        str(tmp_path / "tmp"),
        "--min-free-mb",
        "0",
    ]

    assert main(arguments) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["results"] == {"created": 1, "skipped": 0, "failed": 0}
    assert first["records"][0]["excerpt"] == "derived-search-needle"

    assert main(arguments) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["results"] == {"created": 0, "skipped": 1, "failed": 0}
    assert packet_path.read_bytes() == raw_before
    assert len(list(derived.rglob("ext_*.json"))) == 1

    assert (
        main(
            [
                "search",
                "derived-search-needle",
                "--archive-dir",
                str(archive),
                "--derived-dir",
                str(derived),
            ]
        )
        == 0
    )
    search = json.loads(capsys.readouterr().out)
    assert search["count"] == 1
    assert search["results"][0]["capture_id"] == capture_id


def backlog_arguments(
    archive: Path,
    derived: Path,
    executable: Path,
    tmp_path: Path,
) -> list[str]:
    return [
        "ocr-backlog",
        "--archive-dir",
        str(archive),
        "--derived-dir",
        str(derived),
        "--executable",
        str(executable),
        "--temp-dir",
        str(tmp_path / "tmp"),
        "--min-free-mb",
        "0",
    ]


def test_ocr_backlog_cli_applies_limits_and_resumes(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    create_capture(archive, "one", (b"a", b"b"))
    create_capture(archive, "two", (b"c",))
    create_capture(archive, "three", (b"d", b"e"))
    arguments = backlog_arguments(
        archive,
        derived,
        fake_tesseract(tmp_path / "fake-tesseract"),
        tmp_path,
    )
    arguments.extend(["--capture-limit", "2", "--slide-limit", "3", "--kind", "slideshow"])

    assert main(arguments) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["matched_captures"] == 3
    assert first["selected_captures"] == 2
    assert first["selected_images"] == 3
    assert first["results"] == {"created": 3, "skipped": 0, "failed": 0}
    assert first["item_duration_seconds"]["count"] == 3

    assert main(arguments) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["results"] == {"created": 0, "skipped": 3, "failed": 0}


def test_ocr_backlog_cli_isolates_failures(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    create_capture(archive, "one", (b"good",))
    create_capture(archive, "two", (b"bad",))
    create_capture(archive, "three", (b"fine",))
    arguments = backlog_arguments(
        archive,
        derived,
        fake_tesseract(tmp_path / "fake-tesseract"),
        tmp_path,
    )

    assert main(arguments) == 1
    first = json.loads(capsys.readouterr().out)
    assert first["results"] == {"created": 2, "skipped": 0, "failed": 1}
    assert first["issues"][0]["error"] == "Tesseract exited with status 2"

    assert main(arguments) == 1
    second = json.loads(capsys.readouterr().out)
    assert second["results"] == {"created": 0, "skipped": 2, "failed": 1}


def test_ocr_backlog_cli_saves_reproducible_resume_reports(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    create_capture(archive, "one", (b"good", b"bad"))
    base = backlog_arguments(
        archive,
        derived,
        fake_tesseract(tmp_path / "fake-tesseract"),
        tmp_path,
    )
    first_path = tmp_path / "reports" / "run-1.json"
    second_path = tmp_path / "reports" / "run-2.json"

    assert main([*base, "--report", str(first_path)]) == 1
    first_output = json.loads(capsys.readouterr().out)
    assert first_output["report_path"] == str(first_path)
    first = OcrBacklogReportDocument.model_validate_json(first_path.read_bytes())
    assert first.counts.created == 1
    assert first.counts.failed == 1
    assert first.extractor.version == "fake-tesseract-cli"
    assert first.runtime["timeout_seconds"] == 60
    assert any(item.status == "failed" and item.error for item in first.items)
    assert any(item.status == "created" and item.request_id for item in first.items)
    first_bytes = first_path.read_bytes()

    assert main([*base, "--report", str(second_path)]) == 1
    capsys.readouterr()
    second = OcrBacklogReportDocument.model_validate_json(second_path.read_bytes())
    assert second.counts.skipped == 1
    assert second.counts.failed == 1
    assert first_path.read_bytes() == first_bytes
    assert [item.request_id for item in second.items if item.status == "skipped"] == [
        item.request_id for item in first.items if item.status == "created"
    ]


def test_ocr_evaluate_cli_consumes_saved_batch_report(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    capture_id = create_capture(archive, "one", (b"good",))
    batch_path = tmp_path / "batch.json"
    base = backlog_arguments(
        archive,
        derived,
        fake_tesseract(tmp_path / "fake-tesseract"),
        tmp_path,
    )
    assert main([*base, "--report", str(batch_path)]) == 0
    capsys.readouterr()
    packet = next(
        item
        for item in FileSystemSourcePacketRepository(archive).iter_packets()
        if item.capture_id == capture_id
    )
    samples_path = tmp_path / "samples.json"
    samples_path.write_text(
        json.dumps(
            {
                "schema_version": "ocr-evaluation/v1",
                "samples": [
                    {
                        "sample_id": "golden-1",
                        "capture_id": packet.capture_id,
                        "artifact_id": packet.artifacts[0].artifact_id,
                        "locator": {"kind": "slide", "slide": 1},
                        "expected_text": "derived-search-needle",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "evaluation.json"

    assert (
        main(
            [
                "ocr-evaluate",
                str(samples_path),
                "--derived-dir",
                str(derived),
                "--batch-report",
                str(batch_path),
                "--output",
                str(output_path),
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["evaluated"] == 1
    assert output["summary"]["exact_match_rate"] == 1.0
    assert output["cases"][0]["extractor"]["version"] == "fake-tesseract-cli"
    report = OcrEvaluationReport.model_validate_json(output_path.read_bytes())
    assert report.cases[0].normalized_exact is True


def test_ocr_golden_example_ids_match_archived_capture(tmp_path: Path, capsys) -> None:
    project_root = Path(__file__).parents[2]
    example = project_root / "examples" / "ocr-golden"
    archive = tmp_path / "archive"

    assert (
        main(
            [
                "archive-capture",
                str(example / "manifest.json"),
                "--archive-dir",
                str(archive),
            ]
        )
        == 0
    )
    archived = json.loads(capsys.readouterr().out)
    packets = list(FileSystemSourcePacketRepository(archive).iter_packets())
    assert len(packets) == 1
    packet = packets[0]
    assert packet.capture_id == archived["capture_id"]

    samples = OcrEvaluationSet.model_validate_json((example / "samples.json").read_bytes())
    assert len(samples.samples) == len(packet.artifacts) == 6
    assert len({sample.sample_id for sample in samples.samples}) == 6
    for slide, sample in enumerate(samples.samples, start=1):
        assert sample.capture_id == packet.capture_id
        assert sample.locator.slide == slide
        assert sample.artifact_id == packet.artifacts[slide - 1].artifact_id
        assert packet.artifacts[slide - 1].role is ArtifactRole.ORIGINAL_IMAGE

    expected = json.loads((example / "expected.json").read_text(encoding="utf-8"))
    assert {item["slide"]: item["expected_text"] for item in expected} == {
        sample.locator.slide: sample.expected_text for sample in samples.samples
    }
