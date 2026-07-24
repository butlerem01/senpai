#!/usr/bin/env python3
"""Focused hostile tests for the Omega-decision-v3 terminal lineage."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import copy
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

try:
    from . import omega_decision_v3_terminal_lineage as terminal
except ImportError:  # Trusted sibling import for direct ``python -I`` runs.
    module_directory = str(Path(__file__).resolve().parent)
    if module_directory not in sys.path:
        sys.path.insert(0, module_directory)
    import omega_decision_v3_terminal_lineage as terminal


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value))


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(_canonical(row) for row in rows))


def _identity(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "path": str(path.resolve()),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _native_identity(path: Path) -> dict[str, object]:
    value = _identity(path)
    return {"bytes": value["bytes"], "sha256": value["sha256"]}


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _child_id(root: str, group: str, move: str, ofen: str) -> str:
    return _sha(
        f"{terminal.CHILD_ID_DOMAIN}\0{root}\0{group}\0{move}\0{ofen}"
    )


class TerminalFixture:
    CLAIM_TIME = "2026-07-24T00:00:00.000002Z"
    START_TIME = "2026-07-24T00:00:00.000003Z"
    COMPLETE_TIME = "2026-07-24T00:00:00.000004Z"
    LINEAGE_TIME = "2026-07-24T00:00:00.000005Z"

    def __init__(
        self,
        root: Path,
        *,
        trajectory_pairs: int = 1,
        max_plies: int = 12,
        workers: int = 1,
    ) -> None:
        self.root = root
        self.trajectory_pairs = trajectory_pairs
        self.max_plies = max_plies
        self.workers = workers
        self.data = root / "data"
        self.data.mkdir(parents=True)
        self.history_roots = self.data / "history-roots.jsonl"
        self.history_manifest = Path(str(self.history_roots) + ".manifest.json")
        repository = Path(terminal.__file__).resolve().parents[2]
        frozen = repository / "tools/omega_nnue/frozen_runtime"
        sampler_bundle = frozen / "king-state-v6/history-root-sampler"
        classifier_bundle = frozen / "king-state-v6/terminal-classifier"
        self.sampler = sampler_bundle / "OmegaHistoryRootSamplerG6.dll"
        self.classifier = (
            classifier_bundle / "OmegaTerminalPreclassifierG6.dll"
        )
        self.runner = (
            frozen / "king-state-v5/dotnet-runtime/dotnet.exe"
        )
        self.chesslib = classifier_bundle / "ChessLib.dll"
        self.runtime_manifest = classifier_bundle / "runtime.manifest.json"
        self.bundle_manifest = classifier_bundle / "bundle.manifest.json"
        self.producer = Path(terminal.__file__).resolve()
        self.native_manifest = self.data / "terminal-native.manifest.json"
        self.transcript = self.data / "terminal-transcript.jsonl"
        self.eligible_roots = self.data / "terminal-eligible-roots.jsonl"
        self.eligible_children = self.data / "terminal-eligible-children.jsonl"
        self.stdout = self.data / "terminal.stdout.txt"
        self.stderr = self.data / "terminal.stderr.txt"
        self.claim = self.data / "terminal.claim.json"
        self.lineage = self.data / "terminal.lineage.json"
        self._baseline_documents: dict[str, object] | None = None
        self._write_inputs()
        self.claim_kwargs = {
            "history_roots": self.history_roots,
            "history_roots_manifest": self.history_manifest,
            "classifier_bundle_manifest": self.bundle_manifest,
            "classifier_executable": self.classifier,
            "classifier_runner": self.runner,
            "classifier_runtime_manifest": self.runtime_manifest,
            "chesslib_assembly": self.chesslib,
            "producer": self.producer,
            "planned_native_manifest": self.native_manifest,
            "planned_transcript": self.transcript,
            "planned_eligible_roots": self.eligible_roots,
            "planned_eligible_children": self.eligible_children,
            "planned_stdout": self.stdout,
            "planned_stderr": self.stderr,
            "created_utc": self.CLAIM_TIME,
        }
        terminal.publish_terminal_claim(self.claim, **self.claim_kwargs)

    def _write_inputs(self) -> None:
        command = [
            str(self.runner.resolve()),
            str(self.sampler.resolve()),
            "--output", str(self.history_roots.resolve()),
            "--seed", "2026072400",
            "--trajectory-pairs", str(self.trajectory_pairs),
            "--max-plies", str(self.max_plies),
            "--positions-per-phase-side", "1",
            "--capture-percent", "72",
            "--workers", str(self.workers),
        ]
        completed = subprocess.run(
            command,
            cwd=self.sampler.parent,
            env={},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0 or completed.stderr:
            raise RuntimeError(
                "real history sampler failed: "
                + completed.stderr.decode("utf-8", errors="replace")
            )
        self.source_rows = [
            json.loads(line)
            for line in self.history_roots.read_text(encoding="utf-8").splitlines()
        ]
        if not self.source_rows:
            raise RuntimeError("real history sampler emitted no test rows")
        self.history_manifest_document = terminal.parse_history_sampler_manifest(
            self.history_manifest, self.history_roots
        )

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict]:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
        ]

    def _run_classifier(self, directory: Path) -> tuple[dict[str, object], bytes, bytes]:
        directory.mkdir(parents=True, exist_ok=True)
        transcript = directory / "transcript.jsonl"
        roots = directory / "roots.jsonl"
        children = directory / "children.jsonl"
        manifest = directory / "manifest.json"
        command = [
            str(self.runner.resolve()),
            str(self.classifier.resolve()),
            "--input", str(self.history_roots.resolve()),
            "--transcript", str(transcript.resolve()),
            "--eligible-roots", str(roots.resolve()),
            "--eligible-children", str(children.resolve()),
            "--manifest", str(manifest.resolve()),
        ]
        completed = subprocess.run(
            command,
            cwd=self.classifier.parent,
            env={},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0 or completed.stderr:
            raise RuntimeError(
                "real terminal classifier failed: "
                + completed.stderr.decode("utf-8", errors="replace")
            )
        return (
            {
                "transcript": self._read_jsonl(transcript),
                "roots": self._read_jsonl(roots),
                "children": self._read_jsonl(children),
                "native": json.loads(manifest.read_text(encoding="utf-8")),
            },
            completed.stdout,
            completed.stderr,
        )

    def documents(self) -> dict[str, list[dict]]:
        if self._baseline_documents is None:
            documents, _, _ = self._run_classifier(self.root / "reachable-baseline")
            self._baseline_documents = documents
        return copy.deepcopy(
            {
                "transcript": self._baseline_documents["transcript"],
                "roots": self._baseline_documents["roots"],
                "children": self._baseline_documents["children"],
            }
        )

    def run_claimed_classifier(self) -> dict[str, list[dict]]:
        claim = terminal.verify_terminal_claim(self.claim)
        completed = subprocess.run(
            claim["invocation"]["argv"],
            cwd=claim["invocation"]["cwd"],
            env=claim["invocation"]["environment"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=claim["invocation"]["timeoutSeconds"],
            check=False,
        )
        if completed.returncode != 0 or completed.stderr:
            raise RuntimeError(
                "exact claimed classifier invocation failed: "
                + completed.stderr.decode("utf-8", errors="replace")
            )
        self.stdout.write_bytes(completed.stdout)
        self.stderr.write_bytes(completed.stderr)
        return {
            "transcript": self._read_jsonl(self.transcript),
            "roots": self._read_jsonl(self.eligible_roots),
            "children": self._read_jsonl(self.eligible_children),
        }

    def write_outputs(
        self,
        documents: dict[str, list[dict]] | None = None,
        *,
        native_mutator=None,
    ) -> dict[str, list[dict]]:
        actual = self.run_claimed_classifier()
        native_template = json.loads(
            self.native_manifest.read_text(encoding="utf-8")
        )
        if documents is None and native_mutator is None:
            return actual
        documents = actual if documents is None else documents
        for path in (
            self.transcript,
            self.eligible_roots,
            self.eligible_children,
            self.native_manifest,
            self.stdout,
            self.stderr,
        ):
            path.unlink()
        _write_jsonl(self.transcript, documents["transcript"])
        _write_jsonl(self.eligible_roots, documents["roots"])
        _write_jsonl(self.eligible_children, documents["children"])
        counts = {classification: 0 for classification in terminal.CLASSIFICATIONS}
        for row in documents["transcript"]:
            root = row["root"]
            counts[root["classification"] if root is not None else row["rejection"]] += 1
            for child in row["children"]:
                counts[child["classification"]] += 1
        native = {
            **native_template,
            "coverage": {
                "sourceRecords": len(documents["transcript"]),
                "acceptedRoots": sum(
                    row["teacherEligible"] is True
                    for row in documents["transcript"]
                ),
                "rejectedRoots": sum(
                    row["teacherEligible"] is False
                    for row in documents["transcript"]
                ),
                "eligibleChildren": len(documents["children"]),
                "classificationCounts": counts,
            },
            "input": _native_identity(self.history_roots),
            "transcript": _native_identity(self.transcript),
            "eligibleRoots": _native_identity(self.eligible_roots),
            "eligibleChildren": _native_identity(self.eligible_children),
        }
        if native_mutator is not None:
            native_mutator(native)
        _write_json(self.native_manifest, native)
        coverage = {
            "sourceRecords": native["coverage"]["sourceRecords"],
            "acceptedRoots": native["coverage"]["acceptedRoots"],
            "rejectedRoots": native["coverage"]["rejectedRoots"],
            "eligibleChildren": native["coverage"]["eligibleChildren"],
        }
        self.stdout.write_bytes(terminal._expected_classifier_stdout(
            coverage=coverage,
            transcript=self.transcript,
            eligible_roots=self.eligible_roots,
            eligible_children=self.eligible_children,
            native_manifest=self.native_manifest,
        ))
        self.stderr.write_bytes(b"")
        return documents

    def lineage_kwargs(self, *, created_utc: str | None = None) -> dict:
        return {
            "claim": self.claim,
            "native_manifest": self.native_manifest,
            "transcript": self.transcript,
            "eligible_roots": self.eligible_roots,
            "eligible_children": self.eligible_children,
            "classifier_stdout": self.stdout,
            "classifier_stderr": self.stderr,
            "started_utc": self.START_TIME,
            "completed_utc": self.COMPLETE_TIME,
            "exit_code": 0,
            "timed_out": False,
            "created_utc": created_utc or self.LINEAGE_TIME,
        }


class TerminalLineageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="omega-terminal-lineage-test-"
        )
        multi_batch = (
            self._testMethodName
            == "test_honest_claim_precedes_outputs_and_binds_exact_invocation"
        )
        self.fixture = TerminalFixture(
            Path(self.temporary.name),
            trajectory_pairs=13 if multi_batch else 1,
            max_plies=72 if multi_batch else 12,
            workers=3 if multi_batch else 1,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_honest_claim_precedes_outputs_and_binds_exact_invocation(self) -> None:
        fixture = self.fixture
        for path in (
            fixture.native_manifest,
            fixture.transcript,
            fixture.eligible_roots,
            fixture.eligible_children,
            fixture.stdout,
            fixture.stderr,
        ):
            self.assertFalse(path.exists(), path)
        claim = terminal.verify_terminal_claim(fixture.claim)
        self.assertEqual(claim["invocation"]["environment"], {})
        self.assertEqual(
            claim["invocation"]["argv"],
            [
                str(fixture.runner.resolve()),
                str(fixture.classifier.resolve()),
                "--input", str(fixture.history_roots.resolve()),
                "--transcript", str(fixture.transcript.resolve()),
                "--eligible-roots", str(fixture.eligible_roots.resolve()),
                "--eligible-children", str(fixture.eligible_children.resolve()),
                "--manifest", str(fixture.native_manifest.resolve()),
            ],
        )
        fixture.run_claimed_classifier()
        terminal.publish_terminal_lineage(
            fixture.lineage, **fixture.lineage_kwargs()
        )
        verified = terminal.verify_terminal_lineage(fixture.lineage)
        self.assertEqual(
            verified["coverage"]["sourceRecords"], len(fixture.source_rows)
        )
        policy = fixture.history_manifest_document["policy"]
        self.assertEqual(policy["trajectoryPairs"], 13)
        self.assertEqual(policy["workers"], 3)
        self.assertEqual(policy["maxPlies"], 72)
        self.assertGreater(len(fixture.source_rows), 8)

    def test_valid_claim_and_lineage_recompute_exact_partition(self) -> None:
        fixture = self.fixture
        documents = fixture.write_outputs()
        terminal.publish_terminal_lineage(
            fixture.lineage, **fixture.lineage_kwargs()
        )
        result = terminal.verify_terminal_lineage(fixture.lineage)
        coverage = result["coverage"]
        accepted = sum(
            row["teacherEligible"] is True for row in documents["transcript"]
        )
        rejected = len(documents["transcript"]) - accepted
        terminal_children = sum(
            child["classification"] in terminal.TERMINAL_CLASSES
            for row in documents["transcript"]
            for child in row["children"]
        )
        self.assertEqual(coverage["sourceRecords"], len(fixture.source_rows))
        self.assertEqual(coverage["acceptedRoots"], accepted)
        self.assertEqual(coverage["rejectedRoots"], rejected)
        self.assertEqual(coverage["terminalChildren"], terminal_children)
        self.assertEqual(
            coverage["eligibleChildren"], len(documents["children"])
        )
        self.assertEqual(
            result["terminalChildrenExcludedBeforeRouting"], terminal_children
        )
        self.assertEqual(
            fixture.lineage.read_bytes(), _canonical(result)
        )

    def test_production_lineage_never_calls_materializing_jsonl_helpers(self) -> None:
        fixture = self.fixture
        fixture.write_outputs()
        forbidden = AssertionError("materializing JSONL helper entered production path")
        patches = [
            mock.patch.object(terminal, name, side_effect=forbidden)
            for name in (
                "parse_history_roots",
                "parse_transcript",
                "parse_eligible_roots",
                "parse_eligible_children",
                "_cross_check_artifacts",
                "_load_jsonl",
            )
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        result = terminal.terminal_lineage_document(
            **fixture.lineage_kwargs()
        )
        self.assertEqual(
            result["coverage"]["sourceRecords"], len(fixture.source_rows)
        )

    def test_missing_eligible_root_is_rejected(self) -> None:
        fixture = self.fixture
        documents = fixture.documents()
        self.assertTrue(documents["roots"])
        documents["roots"].pop()
        fixture.write_outputs(documents)
        with self.assertRaisesRegex(ValueError, "exact accepted"):
            terminal.terminal_lineage_document(**fixture.lineage_kwargs())

    def test_extra_eligible_root_is_rejected(self) -> None:
        fixture = self.fixture
        documents = fixture.documents()
        self.assertTrue(documents["roots"])
        documents["roots"].append(copy.deepcopy(documents["roots"][-1]))
        fixture.write_outputs(documents)
        with self.assertRaisesRegex(ValueError, "exact accepted"):
            terminal.terminal_lineage_document(**fixture.lineage_kwargs())

    def test_extra_eligible_child_is_rejected(self) -> None:
        fixture = self.fixture
        documents = fixture.documents()
        self.assertTrue(documents["children"])
        documents["children"].append(copy.deepcopy(documents["children"][-1]))
        fixture.write_outputs(documents)
        with self.assertRaisesRegex(ValueError, "exact accepted"):
            terminal.terminal_lineage_document(**fixture.lineage_kwargs())

    def test_eligible_root_order_substitution_is_rejected(self) -> None:
        fixture = self.fixture
        documents = fixture.documents()
        self.assertGreaterEqual(len(documents["roots"]), 2)
        documents["roots"][0], documents["roots"][1] = (
            documents["roots"][1],
            documents["roots"][0],
        )
        fixture.write_outputs(documents)
        with self.assertRaisesRegex(ValueError, "exact accepted"):
            terminal.terminal_lineage_document(**fixture.lineage_kwargs())

    def test_eligible_child_root_ordinal_order_substitution_is_rejected(self) -> None:
        fixture = self.fixture
        documents = fixture.documents()
        self.assertGreaterEqual(len(documents["children"]), 2)
        documents["children"][0], documents["children"][1] = (
            documents["children"][1],
            documents["children"][0],
        )
        fixture.write_outputs(documents)
        with self.assertRaisesRegex(ValueError, "exact accepted"):
            terminal.terminal_lineage_document(**fixture.lineage_kwargs())

    def test_claim_publication_is_no_clobber_and_deterministic(self) -> None:
        fixture = self.fixture
        before = fixture.claim.read_bytes()
        expected = terminal.verify_terminal_claim(fixture.claim)
        self.assertEqual(before, _canonical(expected))
        with self.assertRaises(FileExistsError):
            terminal.publish_terminal_claim(fixture.claim, **fixture.claim_kwargs)
        self.assertEqual(fixture.claim.read_bytes(), before)

    def test_lineage_publication_is_no_clobber(self) -> None:
        fixture = self.fixture
        fixture.write_outputs()
        terminal.publish_terminal_lineage(
            fixture.lineage, **fixture.lineage_kwargs()
        )
        before = fixture.lineage.read_bytes()
        with self.assertRaises(FileExistsError):
            terminal.publish_terminal_lineage(
                fixture.lineage, **fixture.lineage_kwargs()
            )
        self.assertEqual(fixture.lineage.read_bytes(), before)

    def test_boolean_claim_counter_is_rejected(self) -> None:
        fixture = self.fixture
        claim = json.loads(fixture.claim.read_text(encoding="utf-8"))
        claim["targetRowsDecodedAtClaim"] = False
        substituted = fixture.data / "boolean.claim.json"
        _write_json(substituted, claim)
        with self.assertRaisesRegex(ValueError, "claim header"):
            terminal.verify_terminal_claim(substituted)

    def test_claim_rejects_duplicate_planned_role(self) -> None:
        fixture = self.fixture
        alternate = fixture.root / "alternate"
        alternate.mkdir()
        kwargs = dict(fixture.claim_kwargs)
        kwargs["planned_native_manifest"] = alternate / "native.json"
        kwargs["planned_transcript"] = alternate / "transcript.jsonl"
        kwargs["planned_eligible_roots"] = alternate / "roots.jsonl"
        kwargs["planned_eligible_children"] = alternate / "children.jsonl"
        kwargs["planned_stdout"] = alternate / "stdout.txt"
        kwargs["planned_stderr"] = alternate / "stdout.txt"
        with self.assertRaisesRegex(ValueError, "share a path"):
            terminal.terminal_claim_document(**kwargs)

    def test_claim_invocation_policy_substitution_is_rejected(self) -> None:
        fixture = self.fixture
        claim = json.loads(fixture.claim.read_text(encoding="utf-8"))
        claim["invocation"]["environment"] = {"PATH": "inherited"}
        substituted = fixture.data / "bad-invocation.claim.json"
        _write_json(substituted, claim)
        with self.assertRaisesRegex(ValueError, "invocation.*policy"):
            terminal.verify_terminal_claim(substituted)

    def test_history_manifest_output_substitution_is_rejected(self) -> None:
        fixture = self.fixture
        manifest = json.loads(fixture.history_manifest.read_text(encoding="utf-8"))
        manifest["output"]["sha256"] = "0" * 64
        _write_json(fixture.history_manifest, manifest)
        alternate = fixture.root / "history-substitution-plans"
        alternate.mkdir()
        kwargs = dict(fixture.claim_kwargs)
        kwargs.update({
            "planned_native_manifest": alternate / "native.json",
            "planned_transcript": alternate / "transcript.jsonl",
            "planned_eligible_roots": alternate / "roots.jsonl",
            "planned_eligible_children": alternate / "children.jsonl",
            "planned_stdout": alternate / "stdout.txt",
            "planned_stderr": alternate / "stderr.txt",
        })
        with self.assertRaisesRegex(ValueError, "identity differs"):
            terminal.terminal_claim_document(**kwargs)

    def test_lineage_must_strictly_follow_claim(self) -> None:
        fixture = self.fixture
        fixture.write_outputs()
        with self.assertRaisesRegex(ValueError, "chronology"):
            terminal.terminal_lineage_document(
                **fixture.lineage_kwargs(created_utc=fixture.CLAIM_TIME)
            )

    def test_claimed_output_path_substitution_is_rejected(self) -> None:
        fixture = self.fixture
        fixture.write_outputs()
        alternate = fixture.data / "alternate-transcript.jsonl"
        alternate.write_bytes(fixture.transcript.read_bytes())
        kwargs = fixture.lineage_kwargs()
        kwargs["transcript"] = alternate
        with self.assertRaisesRegex(ValueError, "planned path differs"):
            terminal.terminal_lineage_document(**kwargs)

    def test_transcript_extra_field_is_rejected(self) -> None:
        fixture = self.fixture
        documents = fixture.documents()
        documents["transcript"][0]["unexpected"] = 1
        fixture.write_outputs(documents)
        with self.assertRaisesRegex(ValueError, "fields changed"):
            terminal.terminal_lineage_document(**fixture.lineage_kwargs())

    def test_transcript_history_substitution_is_rejected(self) -> None:
        fixture = self.fixture
        documents = fixture.documents()
        substituted = (
            "5k4/10/10/10/10/10/10/10/10/4K5[-/-/-/-] w - - 0 1"
        )
        documents["transcript"][0]["history"]["initialOfen"] = substituted
        documents["transcript"][0]["history"]["initialOfenSha256"] = _sha(
            substituted
        )
        documents["roots"][0]["initialOfen"] = substituted
        for child in documents["children"]:
            child["initialOfen"] = substituted
        documents["transcript"][0]["transcriptSha256"] = terminal.transcript_seal(
            documents["transcript"][0]
        )
        documents["roots"][0]["preclassificationTranscriptSha256"] = (
            documents["transcript"][0]["transcriptSha256"]
        )
        for child in documents["children"]:
            child["preclassificationTranscriptSha256"] = (
                documents["transcript"][0]["transcriptSha256"]
            )
        fixture.write_outputs(documents)
        with self.assertRaisesRegex(ValueError, "differs from history input"):
            terminal.terminal_lineage_document(**fixture.lineage_kwargs())

    def test_missing_eligible_child_is_rejected(self) -> None:
        fixture = self.fixture
        documents = fixture.documents()
        documents["children"].pop()
        fixture.write_outputs(documents)
        with self.assertRaisesRegex(ValueError, "header changed|exact accepted"):
            terminal.terminal_lineage_document(**fixture.lineage_kwargs())

    def test_eligible_child_ofen_substitution_is_rejected(self) -> None:
        fixture = self.fixture
        documents = fixture.documents()
        documents["children"][0]["childOfen"] = (
            "4k5/10/10/10/10/10/10/10/10/4K5[-/-/-/-] b - - 99 1"
        )
        fixture.write_outputs(documents)
        with self.assertRaisesRegex(
            ValueError, "child side did not alternate|OFEN hash changed"
        ):
            terminal.terminal_lineage_document(**fixture.lineage_kwargs())

    def test_eligible_child_content_derived_id_substitution_is_rejected(self) -> None:
        fixture = self.fixture
        documents = fixture.documents()
        documents["children"][0]["childId"] = "0" * 64
        fixture.write_outputs(documents)
        with self.assertRaisesRegex(ValueError, "stable child ID"):
            terminal.terminal_lineage_document(**fixture.lineage_kwargs())

    def test_rejected_root_child_undercoverage_is_rejected(self) -> None:
        fixture = self.fixture
        documents = fixture.documents()
        documents["transcript"][1]["children"].pop()
        documents["transcript"][1]["transcriptSha256"] = terminal.transcript_seal(
            documents["transcript"][1]
        )
        fixture.write_outputs(documents)
        with self.assertRaisesRegex(ValueError, "legal-child coverage"):
            terminal.terminal_lineage_document(**fixture.lineage_kwargs())

    def test_forged_transcript_seal_is_rejected(self) -> None:
        fixture = self.fixture
        documents = fixture.documents()
        documents["transcript"][0]["transcriptSha256"] = _sha("forged")
        fixture.write_outputs(documents)
        with self.assertRaisesRegex(ValueError, "transcript seal"):
            terminal.terminal_lineage_document(**fixture.lineage_kwargs())

    def test_failure_ply_outside_history_is_rejected(self) -> None:
        fixture = self.fixture
        documents = fixture.documents()
        row = documents["transcript"][1]
        row["root"] = None
        row["children"] = []
        row["rejection"] = "replay-failure"
        row["history"]["observedPlyOfenSha256"] = []
        row["history"]["verifiedPlies"] = 0
        row["history"]["failurePly"] = 999
        row["history"]["failureMove"] = None
        row["history"]["failureCode"] = "invented-failure"
        row["transcriptSha256"] = terminal.transcript_seal(row)
        fixture.write_outputs(documents)
        with self.assertRaisesRegex(ValueError, "failure ply exceeds"):
            terminal.terminal_lineage_document(**fixture.lineage_kwargs())

    def test_sampler_phase_substitution_is_rejected(self) -> None:
        fixture = self.fixture
        documents = fixture.documents()
        late = (
            "4k5/pppppppppp/10/10/10/10/10/10/PPPPPPPP2/4K5[-/-/-/-] "
            "w - - 0 4"
        )
        row = documents["transcript"][0]
        row["root"]["ofen"] = late
        row["root"]["ofenSha256"] = _sha(late)
        row["transcriptSha256"] = terminal.transcript_seal(row)
        fixture.write_outputs(documents)
        with self.assertRaisesRegex(
            ValueError, "side-to-move changed|phase/side differs"
        ):
            terminal.terminal_lineage_document(**fixture.lineage_kwargs())

    def test_boolean_native_coverage_is_rejected(self) -> None:
        fixture = self.fixture
        fixture.write_outputs(
            native_mutator=lambda native: native["coverage"].__setitem__(
                "sourceRecords", False
            )
        )
        with self.assertRaisesRegex(ValueError, "must be an integer"):
            terminal.terminal_lineage_document(**fixture.lineage_kwargs())

    def test_native_classifier_substitution_is_rejected(self) -> None:
        fixture = self.fixture
        fixture.write_outputs(
            native_mutator=lambda native: native["runtime"].__setitem__(
                "executionClosure",
                {
                    **native["runtime"]["executionClosure"],
                    "classifierAssembly": {
                        **native["runtime"]["executionClosure"][
                            "classifierAssembly"
                        ],
                        "sha256": "0" * 64,
                    },
                },
            )
        )
        with self.assertRaisesRegex(
            ValueError, "identity differs|pinned identity|classifier differs"
        ):
            terminal.terminal_lineage_document(**fixture.lineage_kwargs())

    def test_claimed_input_mutation_is_rejected(self) -> None:
        fixture = self.fixture
        fixture.history_roots.write_bytes(
            fixture.history_roots.read_bytes() + b"{}\n"
        )
        with self.assertRaisesRegex(ValueError, "identity differs"):
            fixture.run_claimed_classifier()

    def test_synchronized_classifier_and_bundle_substitution_is_rejected(self) -> None:
        fixture = self.fixture
        payload = bytearray(fixture.classifier.read_bytes())
        payload[-1] ^= 1
        substituted = fixture.data / "substituted-classifier.dll"
        substituted.write_bytes(bytes(payload))
        bundle = json.loads(fixture.bundle_manifest.read_text(encoding="utf-8"))
        bundle["classifierAssembly"] = {
            "relativePath": substituted.name,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        substituted_bundle = fixture.data / "substituted-bundle.manifest.json"
        _write_json(substituted_bundle, bundle)
        alternate = fixture.root / "substitution-plans"
        alternate.mkdir()
        kwargs = dict(fixture.claim_kwargs)
        kwargs["classifier_executable"] = substituted
        kwargs["classifier_bundle_manifest"] = substituted_bundle
        kwargs.update({
            "planned_native_manifest": alternate / "native.json",
            "planned_transcript": alternate / "transcript.jsonl",
            "planned_eligible_roots": alternate / "roots.jsonl",
            "planned_eligible_children": alternate / "children.jsonl",
            "planned_stdout": alternate / "stdout.txt",
            "planned_stderr": alternate / "stderr.txt",
        })
        with self.assertRaisesRegex(ValueError, "relative path|pinned identity"):
            terminal.terminal_claim_document(**kwargs)

    def test_hardlinked_output_roles_are_rejected(self) -> None:
        fixture = self.fixture
        fixture.write_outputs()
        fixture.stdout.unlink()
        try:
            os.link(fixture.eligible_children, fixture.stdout)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"hardlinks unavailable: {error}")
        with self.assertRaisesRegex(ValueError, "regular|inode"):
            terminal.terminal_lineage_document(**fixture.lineage_kwargs())

    def test_nonempty_classifier_stderr_is_rejected(self) -> None:
        fixture = self.fixture
        fixture.write_outputs()
        fixture.stderr.write_bytes(b"unexpected diagnostic\r\n")
        with self.assertRaisesRegex(ValueError, "stdout/stderr policy"):
            terminal.terminal_lineage_document(**fixture.lineage_kwargs())

    def test_failed_publication_cleanup_never_unlinks_named_evidence(self) -> None:
        fixture = self.fixture
        evidence = fixture.data / "failed-publication.json"
        evidence.write_bytes(b"owned bytes\n")
        expected = _identity(evidence)
        evidence.write_bytes(b"replacement evidence\n")
        terminal._delete_if_identity(evidence, expected)
        self.assertEqual(evidence.read_bytes(), b"replacement evidence\n")

    def test_boolean_lineage_counter_is_rejected_after_rebuilt_file(self) -> None:
        fixture = self.fixture
        fixture.write_outputs()
        terminal.publish_terminal_lineage(
            fixture.lineage, **fixture.lineage_kwargs()
        )
        document = json.loads(fixture.lineage.read_text(encoding="utf-8"))
        document["unclassifiedChildren"] = False
        substituted = fixture.data / "boolean.lineage.json"
        _write_json(substituted, document)
        with self.assertRaisesRegex(ValueError, "lineage header"):
            terminal.verify_terminal_lineage(substituted)


if __name__ == "__main__":
    unittest.main(verbosity=2)
