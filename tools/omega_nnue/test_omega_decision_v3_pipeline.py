#!/usr/bin/env python3
"""Focused tests for the immutable Omega decision-v3 pipeline."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import omega_decision_v3_pipeline as pipeline


TIME = "2026-07-24T08:00:00.000000Z"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pipeline._canonical_json(value))


class PlanFixture:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.authority = (self.root / "authority").resolve()
        self.external = (self.root / "external").resolve()
        self.authority.mkdir(parents=True)
        self.external.mkdir(parents=True)
        self.files: dict[str, Path] = {}
        for index, name in enumerate(
            (
                "history-sampler.dll", "classifier-bundle.json",
                "classifier.dll", "dotnet.exe", "runtime.json",
                "ChessLib.dll", "teacher.exe", "python.exe",
                "prior-g3.json", "prior-g4g5.json",
            )
        ):
            path = (self.external / name).resolve()
            path.write_bytes(f"fixture-{index}-{name}\n".encode("utf-8"))
            self.files[name] = path
        self.inputs = (self.root / "inputs.json").resolve()
        self.inputs_document = {
            "schemaVersion": 1,
            "kind": pipeline.INPUT_KIND,
            "profileId": pipeline.PROFILE_ID,
            "authorityDirectory": str(self.authority),
            "historySamplerExecutable": str(self.files["history-sampler.dll"]),
            "classifierBundleManifest": str(self.files["classifier-bundle.json"]),
            "classifierExecutable": str(self.files["classifier.dll"]),
            "classifierRunner": str(self.files["dotnet.exe"]),
            "classifierRuntimeManifest": str(self.files["runtime.json"]),
            "chessLibAssembly": str(self.files["ChessLib.dll"]),
            "priorForbiddenGroups": [
                {"coveredSourceIds": ["G3"], "manifest": str(self.files["prior-g3.json"])},
                {"coveredSourceIds": ["G4", "G5"], "manifest": str(self.files["prior-g4g5.json"])},
            ],
            "teacherEngine": str(self.files["teacher.exe"]),
            "staticHceExecutable": str(Path(sys.executable).resolve()),
        }
        _write_json(self.inputs, self.inputs_document)
        self.plan = (self.authority / pipeline.CANONICAL_RELATIVE_PATHS["plan"]).resolve()

    def publish(self) -> dict:
        return pipeline.publish_plan(self.inputs, self.plan, created_utc=TIME)


class PipelinePlanTests(unittest.TestCase):
    def test_plan_freezes_exact_stop_gated_runbook_without_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PlanFixture(Path(directory))
            plan = fixture.publish()
            self.assertEqual(plan["targetRowsDecodedAtPlan"], 0)
            self.assertEqual(plan["targetFieldsDecodedAtPlan"], 0)
            self.assertIs(plan["resultInformationRead"], False)
            self.assertEqual(
                plan["productionInventoryContract"],
                pipeline.PRODUCTION_INVENTORY_CONTRACT,
            )
            stages = [entry["stage"] for entry in plan["runbook"]]
            self.assertEqual(
                stages,
                [
                    "history", "terminal-claim", "terminal-classifier",
                    "terminal-finalize", "routing", "routing-finalize",
                    "initializer", "pretarget-claim", "pretarget-hce",
                    "pretarget-finalize", "teacher-claim", "teacher-run",
                    "materialize-projection", "teacher-finalize",
                    "capsule-finalize",
                ],
            )
            self.assertEqual([entry["sequence"] for entry in plan["runbook"]], list(range(10, 151, 10)))
            boundary = next(item for item in plan["runbook"] if item["stage"] == "pretarget-finalize")
            self.assertIn("teacher target-bearing work remains forbidden", boundary["stopAfter"])
            teacher = next(item for item in plan["runbook"] if item["stage"] == "teacher-run")
            self.assertEqual(teacher["argv"][1:3], ["-I", "-B"])
            self.assertIn("resumeArgv", teacher)

    def test_plan_resume_is_exact_and_does_not_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PlanFixture(Path(directory))
            first = fixture.publish()
            identity = pipeline._identity(fixture.plan)
            second = fixture.publish()
            self.assertEqual(first, second)
            self.assertEqual(identity, pipeline._identity(fixture.plan))

    def test_plan_refuses_same_path_with_different_chronology(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PlanFixture(Path(directory))
            fixture.publish()
            with self.assertRaisesRegex(ValueError, "differs from requested"):
                pipeline.publish_plan(
                    fixture.inputs,
                    fixture.plan,
                    created_utc="2026-07-24T08:00:00.000001Z",
                )

    def test_plan_rejects_noncanonical_prior_source_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PlanFixture(Path(directory))
            fixture.inputs_document["priorForbiddenGroups"][0]["coveredSourceIds"] = ["G4"]
            fixture.inputs_document["priorForbiddenGroups"][1]["coveredSourceIds"] = ["G3", "G5"]
            _write_json(fixture.inputs, fixture.inputs_document)
            with self.assertRaisesRegex(ValueError, "exactly G3,G4,G5"):
                fixture.publish()

    def test_plan_rejects_substituted_hce_or_verifier_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PlanFixture(Path(directory))
            fixture.inputs_document["staticHceExecutable"] = str(
                fixture.files["python.exe"]
            )
            _write_json(fixture.inputs, fixture.inputs_document)
            with self.assertRaisesRegex(ValueError, "exact Python runtime"):
                fixture.publish()

    def test_verify_plan_detects_runbook_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PlanFixture(Path(directory))
            fixture.publish()
            value = json.loads(fixture.plan.read_text(encoding="utf-8"))
            value["runbook"][0]["argv"][-1] = "99"
            fixture.plan.write_bytes(pipeline._canonical_json(value))
            with self.assertRaisesRegex(ValueError, "runbook changed"):
                pipeline.verify_plan(fixture.plan)

    def test_verify_plan_rejects_status_not_derived_from_exact_pins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PlanFixture(Path(directory))
            fixture.publish()
            value = json.loads(fixture.plan.read_text(encoding="utf-8"))
            value["status"] = (
                "awaiting-reviewed-target-free-pins"
                if value["status"] == "ready-for-target-free-runbook"
                else "ready-for-target-free-runbook"
            )
            fixture.plan.write_bytes(pipeline._canonical_json(value))
            with self.assertRaisesRegex(ValueError, "status differs"):
                pipeline.verify_plan(fixture.plan)

    def test_status_advances_past_routing_initializer_and_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PlanFixture(Path(directory))
            plan = fixture.publish()
            paths = {name: Path(path) for name, path in plan["paths"].items()}

            def realize(name: str) -> None:
                paths[name].parent.mkdir(parents=True, exist_ok=True)
                paths[name].write_bytes(b"fixture\n")

            realize("routingCompletion")
            self.assertEqual(pipeline.status_document(fixture.plan)["nextStage"], "initializer")
            for name in (
                "initializerSelection", "initializerClosure", "initializerModel",
                "initializerManifest",
            ):
                realize(name)
            self.assertEqual(pipeline.status_document(fixture.plan)["nextStage"], "pretarget-claim")
            realize("preTargetHceCompletion")
            self.assertEqual(pipeline.status_document(fixture.plan)["nextStage"], "teacher-claim")
            realize("teacherClaim")
            self.assertEqual(pipeline.status_document(fixture.plan)["nextStage"], "teacher-run")
            realize("teacherAttemptLedger")
            self.assertEqual(
                pipeline.status_document(fixture.plan)["nextStage"],
                "teacher-resume-or-materialize-projection",
            )
            realize("teacherManifest")
            self.assertEqual(
                pipeline.status_document(fixture.plan)["nextStage"],
                "materialize-projection",
            )
            realize("projectedCorpus")
            self.assertEqual(pipeline.status_document(fixture.plan)["nextStage"], "teacher-finalize")

    def test_final_authority_pins_are_exact_and_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PlanFixture(Path(directory))
            plan = fixture.publish()
            expected = {
                "trainerAuthority": (
                    330_787,
                    "81b9e0c5ffa5d78a4cf2198781ceffea7649bdaed3e827556a5e3deaeba8a2e0",
                ),
                "initializerGenerator": (
                    19_564,
                    "38d81f665d0bccb4939e3a1707b7dbfbe4c9c29795e0699f51d92bf30af94be0",
                ),
                "verifierImplementation": (
                    151_065,
                    "e0704ade94df4c7d957413101f45fed8ec3c2d2662a7a81e9dfabffa9be10576",
                ),
                "verifierRunner": (
                    4_856,
                    "5f6fb24275d7dc71eb6fa3ef757a1b3ef789211ad3c4346cade941dd63c85300",
                ),
            }
            for name, (size, sha256) in expected.items():
                record = plan["dependencies"][name]
                self.assertIs(record["pinFinalized"], True)
                self.assertIs(record["matches"], True)
                self.assertEqual(record["expectedBytes"], size)
                self.assertEqual(record["expectedSha256"], sha256)
                self.assertEqual(record["actual"]["bytes"], size)
                self.assertEqual(record["actual"]["sha256"], sha256)
            self.assertTrue(
                all(
                    record["pinFinalized"] is True and record["matches"] is True
                    for record in plan["dependencies"].values()
                )
            )
            pipeline._require_dependencies(plan, tuple(expected))

    def test_authority_substitution_fails_before_pretarget_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PlanFixture(Path(directory))
            plan = fixture.publish()
            trainer_path = (
                pipeline._tool_dir()
                / pipeline.REVIEWED_PINS["trainerAuthority"][0]
            ).resolve()
            real_identity = pipeline._identity

            def substituted(path: Path) -> dict:
                identity = real_identity(path)
                if Path(path).resolve() == trainer_path:
                    return {**identity, "bytes": identity["bytes"] + 1}
                return identity

            with mock.patch.object(pipeline, "_identity", side_effect=substituted):
                with self.assertRaisesRegex(
                    ValueError, "pipeline dependency trainerAuthority changed after freeze"
                ):
                    pipeline.claim_stage(fixture.plan, "pretarget", created_utc=TIME)
            for name in (
                "priorForbiddenRegistry", "prelabelSeal", "staticHceOptions",
                "preTargetHceClaim",
            ):
                self.assertFalse(Path(plan["paths"][name]).exists())

    def test_teacher_claim_gate_checks_completed_hce_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PlanFixture(Path(directory))
            plan = fixture.publish()
            calls: list[str] = []

            class FakeTrainer:
                @staticmethod
                def _verify_upstream_hce_completion(*args, **kwargs):
                    calls.append("hce")
                    raise ValueError("missing exact pretarget completion")

            class FakeTeacher:
                @staticmethod
                def publish_teacher_options(*args, **kwargs):
                    calls.append("teacher-options")

                @staticmethod
                def publish_teacher_claim(*args, **kwargs):
                    calls.append("teacher-claim")

            with mock.patch.object(pipeline, "_require_dependencies"), mock.patch.object(
                pipeline,
                "_import_authorities",
                return_value=(FakeTrainer, object(), FakeTeacher, object()),
            ):
                with self.assertRaisesRegex(ValueError, "missing exact pretarget"):
                    pipeline.claim_stage(fixture.plan, "teacher", created_utc=TIME)
            self.assertEqual(calls, ["hce"])
            self.assertFalse(Path(plan["paths"]["teacherClaim"]).exists())


class InventoryTests(unittest.TestCase):
    @staticmethod
    def _routing_authority() -> tuple[dict[str, dict], dict[str, dict]]:
        routes: dict[str, dict] = {}
        components: dict[str, dict] = {}
        ordinal = 0
        for split in pipeline.SPLITS:
            for cell in pipeline.CELLS:
                phase, side = cell.split(":")
                for _ in range(pipeline.ROOTS_PER_CELL[split]):
                    root = f"root-{ordinal:06d}"
                    routes[root] = {
                        "phase": phase,
                        "parentSideToMove": side,
                        "children": [{"childId": f"{root}-{index}"} for index in range(4)],
                    }
                    components[root] = {"split": split}
                    ordinal += 1
        return routes, components

    def test_exact_production_routing_inventory_is_accepted(self) -> None:
        routes, components = self._routing_authority()
        fake = types.SimpleNamespace(
            _parse_target_free_routing=lambda path: routes,
            _parse_component_map=lambda path: components,
        )
        pipeline._require_routing_inventory(
            fake,
            {"targetFreeRouting": Path("routing"), "componentMap": Path("components")},
        )
        self.assertEqual(len(routes), 6144)

    def test_one_missing_cell_root_is_rejected(self) -> None:
        routes, components = self._routing_authority()
        removed = next(iter(routes))
        routes.pop(removed)
        components.pop(removed)
        fake = types.SimpleNamespace(
            _parse_target_free_routing=lambda path: routes,
            _parse_component_map=lambda path: components,
        )
        with self.assertRaisesRegex(ValueError, "phase/side quotas"):
            pipeline._require_routing_inventory(
                fake,
                {"targetFreeRouting": Path("routing"), "componentMap": Path("components")},
            )

    def test_capsule_inventory_requires_4096_1024_1024_roots(self) -> None:
        label = {
            "childrenPerRoot": 4,
            "rows": 24576,
            "rootInventories": {
                split: {
                    "roots": pipeline.ROOTS_PER_SPLIT[split],
                    "children": pipeline.ROOTS_PER_SPLIT[split] * 4,
                    "sha256": "0" * 64,
                }
                for split in pipeline.SPLITS
            },
            "phaseSideInventories": {
                split: {
                    cell: {"roots": pipeline.ROOTS_PER_CELL[split], "sha256": "0" * 64}
                    for cell in pipeline.CELLS
                }
                for split in pipeline.SPLITS
            },
        }
        pipeline._require_capsule_inventories(label)
        label["phaseSideInventories"]["heldOut"]["endgame:b"]["roots"] = 127
        with self.assertRaisesRegex(ValueError, "heldOut/endgame:b"):
            pipeline._require_capsule_inventories(label)


class MaterializeHceTests(unittest.TestCase):
    def test_existing_hce_transcript_must_equal_fresh_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            transcript = (root / "hce.jsonl").resolve()
            transcript.write_text("wrong\n", encoding="utf-8")
            paths = {
                name: (root / f"{name}.fixture").resolve()
                for name in pipeline.CANONICAL_RELATIVE_PATHS
            }
            paths["staticHceTranscript"] = transcript
            plan = {
                "paths": {name: str(path) for name, path in paths.items()},
                "bindings": {"staticHceExecutable": {"path": str(root / "python"), "bytes": 1, "sha256": "0" * 64}},
                "dependencies": {"evaluatorRunner": {"actual": {"path": str(root / "runner"), "bytes": 1, "sha256": "0" * 64}}},
            }

            class FakeTrainer:
                _verify_upstream_hce_claim = staticmethod(lambda *args, **kwargs: {})
                _parse_target_free_routing = staticmethod(lambda path: {"r": {}})
                _target_free_hce_order = staticmethod(
                    lambda routes: [{"childId": "c", "normalizedChildOfen": "ofen w - - 0 1"}]
                )
                _run_exact_evaluator = staticmethod(lambda **kwargs: {"0": 17})

            with mock.patch.object(pipeline, "verify_plan", return_value=plan), mock.patch.object(
                pipeline, "_require_dependencies"
            ), mock.patch.object(
                pipeline, "_import_authorities", return_value=(FakeTrainer, object(), object(), object())
            ):
                with self.assertRaisesRegex(ValueError, "differs from deterministic replay"):
                    pipeline.materialize_hce(root / "plan")


class ProjectionTests(unittest.TestCase):
    def test_projection_delegates_exact_rows_manifest_and_replay_to_teacher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            paths = {
                name: (root / relative).resolve()
                for name, relative in pipeline.CANONICAL_RELATIVE_PATHS.items()
            }
            plan = {"paths": {name: str(path) for name, path in paths.items()}}
            calls: list[str] = []
            projected = {"schemaVersion": 1, "kind": "exact-projected-row", "childId": "c"}

            class FakeTeacher:
                @staticmethod
                def publish_teacher_outputs(**kwargs):
                    calls.append("publish-teacher-outputs")
                    return {}, [{"rootId": "r", "childId": "c"}], {}

                @staticmethod
                def _verify_claim(path):
                    calls.append("verify-claim")
                    return types.SimpleNamespace(components={"r": {"split": "train"}})

                @staticmethod
                def _projected_rows(rows, components):
                    calls.append("projected-rows")
                    self.assertEqual(rows[0]["childId"], "c")
                    self.assertIn("r", components)
                    return [projected]

                @staticmethod
                def projected_manifest_document(**kwargs):
                    calls.append("projection-manifest")
                    self.assertEqual(kwargs["created_utc"], TIME)
                    self.assertTrue(paths["projectedCorpus"].is_file())
                    return {"createdUtc": TIME, "rows": 1, "teacherSemantics": True}

                @staticmethod
                def _verify_projection(**kwargs):
                    calls.append("verify-projection")
                    document, identity = pipeline._load_json(
                        paths["labelManifest"], "test projection manifest"
                    )
                    return pipeline._identity(paths["projectedCorpus"]), identity, document

            with mock.patch.object(pipeline, "verify_plan", return_value=plan), mock.patch.object(
                pipeline, "_require_dependencies"
            ), mock.patch.object(
                pipeline, "_import_authorities", return_value=(None, None, FakeTeacher, None)
            ), mock.patch.object(
                pipeline, "_require_capsule_inventories"
            ), mock.patch.dict(
                pipeline.PRODUCTION_INVENTORY_CONTRACT, {"totalChildren": 1}
            ):
                result = pipeline.materialize_projection(root / "plan", created_utc=TIME)
            self.assertEqual(result["rows"], 1)
            self.assertIs(result["projectionSemanticsVerified"], True)
            self.assertIs(result["resultInformationRead"], False)
            self.assertEqual(
                calls,
                [
                    "publish-teacher-outputs", "verify-claim", "projected-rows",
                    "projection-manifest", "verify-projection",
                ],
            )


class PublicationBoundaryTests(unittest.TestCase):
    def test_subprocess_capture_is_hard_capped(self) -> None:
        command = [sys.executable, "-I", "-B", "-c", "import sys;sys.stdout.write('x'*100)"]
        with mock.patch.object(pipeline, "MAX_PROCESS_STDOUT_BYTES", 32):
            with self.assertRaisesRegex(ValueError, "oversized output"):
                pipeline._run_bounded(command, timeout=30)

    def test_exclusive_publication_never_clobbers_existing_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = (Path(directory) / "artifact.bin").resolve()
            path.write_bytes(b"original")
            with self.assertRaises(FileExistsError):
                pipeline._exclusive_bytes(path, b"replacement")
            self.assertEqual(path.read_bytes(), b"original")

    def test_partial_publication_is_retained_as_evidence_and_not_retried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = (Path(directory) / "artifact.bin").resolve()
            real_write = os.write
            writes = 0

            def interrupted(descriptor: int, payload: bytes) -> int:
                nonlocal writes
                writes += 1
                if writes == 1:
                    return real_write(descriptor, payload[:3])
                raise OSError("injected interruption")

            with mock.patch.object(pipeline.os, "write", side_effect=interrupted):
                with self.assertRaisesRegex(OSError, "injected interruption"):
                    pipeline._exclusive_bytes(path, b"abcdef")
            self.assertTrue(path.exists())
            self.assertEqual(path.read_bytes(), b"abc")
            with self.assertRaises(FileExistsError):
                pipeline._exclusive_bytes(path, b"abcdef")

    def test_hardlinked_existing_output_is_rejected_without_unlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source.bin"
            target = root / "target.bin"
            source.write_bytes(b"same")
            try:
                os.link(source, target)
            except OSError as error:  # pragma: no cover - platform policy
                self.skipTest(str(error))
            with self.assertRaisesRegex(ValueError, "private regular file"):
                pipeline._publish_or_verify_bytes(target, b"same", "hardlink test")
            self.assertTrue(source.exists())
            self.assertTrue(target.exists())

    def test_launch_descriptor_detects_same_path_content_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = (Path(directory) / "runner.py").resolve()
            path.write_bytes(b"original")
            bindings = pipeline._open_launch_bindings({"runner": path})
            try:
                path.write_bytes(b"mutated!")
                with self.assertRaisesRegex(ValueError, "changed during subprocess"):
                    pipeline._recheck_launch_bindings(bindings)
            finally:
                pipeline._close_launch_bindings(bindings)

    def test_failed_fresh_verifier_retains_immutable_capsule_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            paths = {
                name: (root / relative).resolve()
                for name, relative in pipeline.CANONICAL_RELATIVE_PATHS.items()
            }
            options = {"schemaVersion": 1, "kind": "test-verifier-options"}
            _write_json(paths["verifierOptions"], options)
            plan = {
                "paths": {name: str(path) for name, path in paths.items()},
                "bindings": {"staticHceExecutable": {"path": str(root / "python")}},
                "dependencies": {
                    "verifierRunner": {"actual": {"path": str(root / "runner")}},
                    "evaluatorRunner": {"actual": {"path": str(root / "evaluator")}},
                },
            }
            calls: list[str] = []

            class FakeTrainer:
                _verify_initializer_manifest = staticmethod(lambda *args: calls.append("initializer"))

                @staticmethod
                def expected_static_hce_manifest(**kwargs):
                    return {"createdUtc": TIME, "kind": "static-HCE-test"}

            class FakeRouting:
                verify_completion = staticmethod(lambda *args: calls.append("routing"))

            class FakeTeacher:
                _verify_claim = staticmethod(lambda *args: calls.append("teacher-claim"))
                finalize_teacher = staticmethod(lambda **kwargs: calls.append("teacher-finalize"))

            class FakeTerminal:
                verify_terminal_lineage = staticmethod(lambda *args: calls.append("terminal"))

            fake_verifier = types.SimpleNamespace(VERIFIER_OPTIONS=options)
            with mock.patch.object(pipeline, "verify_plan", return_value=plan), mock.patch.object(
                pipeline, "_require_dependencies"
            ), mock.patch.object(
                pipeline, "_import_authorities",
                return_value=(FakeTrainer, FakeRouting, FakeTeacher, FakeTerminal),
            ), mock.patch.object(
                pipeline, "_require_fresh_verifier_ready"
            ), mock.patch.object(
                pipeline, "_require_routing_inventory"
            ), mock.patch.object(
                pipeline, "_verify_initializer_source_authority"
            ), mock.patch.object(
                pipeline, "_label_manifest", return_value={"createdUtc": TIME}
            ), mock.patch.object(
                pipeline, "_require_after"
            ), mock.patch.object(
                pipeline, "_capsule_document", return_value={"createdUtc": TIME, "sealed": True}
            ), mock.patch.object(
                pipeline, "_load_exact_reviewed", return_value=fake_verifier
            ), mock.patch.object(
                pipeline, "_fresh_verifier", side_effect=ValueError("hostile verifier")
            ):
                with self.assertRaisesRegex(ValueError, "hostile verifier"):
                    pipeline.finalize_stage(
                        root / "plan", "capsule", created_utc=TIME
                    )
            self.assertTrue(paths["capsule"].is_file())
            self.assertEqual(
                json.loads(paths["capsule"].read_text(encoding="utf-8")),
                {"createdUtc": TIME, "sealed": True},
            )
            self.assertLess(calls.index("teacher-finalize"), len(calls))


class FreshVerifierReceiptTests(unittest.TestCase):
    @staticmethod
    def _fixture(root: Path) -> tuple[dict, dict, dict[str, Path], dict]:
        root = root.resolve()

        def artifact(name: str, payload: bytes | None = None) -> dict:
            path = (root / name).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload if payload is not None else (name + "\n").encode())
            return pipeline._identity(path)

        identities = {
            name: artifact(f"artifacts/{name}.bin")
            for name in (
                "executable", "runner", "options", "initializerModel", "terminalLineage",
                "priorRegistry", "priorG3", "priorG45", "componentMap", "hceClaim",
                "hceTranscript", "teacherClaim", "prelabel", "routing", "hceEngine",
                "hceRunner", "hceOptions", "teacherLedger", "teacherLedgerCompletion",
                "teacherCompletion", "projectionProducer", "projectedCorpus", "labelManifest",
            )
        }
        initializer_path = (root / "initializer.json").resolve()
        _write_json(
            initializer_path,
            {"selectionMode": "deterministic-fallback", "selectedCatalogIndex": None},
        )
        identities["initializerManifest"] = pipeline._identity(initializer_path)
        routing_completion_path = (root / "routing-completion.json").resolve()
        _write_json(routing_completion_path, {"coverage": {"selectedComponents": 2048}})
        hce_completion_path = (root / "hce-completion.json").resolve()
        _write_json(hce_completion_path, {"inputOrderSha256": "a" * 64})
        identities["hceCompletion"] = pipeline._identity(hce_completion_path)

        capsule_path = (root / "capsule.json").resolve()
        budgets = {
            "shallowNodes": 2000, "deepNodes": 50000, "timeoutSeconds": 180,
            "maximumAttempts": 3, "workers": 4, "childrenPerRoot": 4,
        }
        capsule = {
            "upstreamVerifierExecutable": identities["executable"],
            "upstreamVerifierRunner": identities["runner"],
            "upstreamVerifierOptions": identities["options"],
            "initializerManifest": identities["initializerManifest"],
            "initializerModel": identities["initializerModel"],
            "terminalClassifierLineage": identities["terminalLineage"],
            "priorForbiddenCatalogs": [identities["priorG3"], identities["priorG45"]],
            "priorForbiddenRegistry": identities["priorRegistry"],
            "componentMap": identities["componentMap"],
            "preTargetHceClaim": identities["hceClaim"],
            "preTargetHceCompletion": identities["hceCompletion"],
            "teacherClaim": identities["teacherClaim"],
            "prelabelSeal": identities["prelabel"],
            "targetFreeRouting": identities["routing"],
            "staticHceEngine": identities["hceEngine"],
            "staticHceRunner": identities["hceRunner"],
            "staticHceOptions": identities["hceOptions"],
            "staticHceTranscript": identities["hceTranscript"],
            "teacherAttemptLedger": identities["teacherLedger"],
            "teacherAttemptLedgerCompletion": identities["teacherLedgerCompletion"],
            "teacherCompletion": identities["teacherCompletion"],
            "teacherBudgets": budgets,
            "plannedProjectionProducer": identities["projectionProducer"],
            "plannedProjectedCorpusPath": identities["projectedCorpus"]["path"],
            "plannedProjectionManifestPath": identities["labelManifest"]["path"],
            "projectionProducer": identities["projectionProducer"],
            "projectedCorpus": identities["projectedCorpus"],
            "labelManifest": identities["labelManifest"],
        }
        _write_json(capsule_path, capsule)
        identities["capsule"] = pipeline._identity(capsule_path)

        paths = {
            "capsule": capsule_path,
            "initializerManifest": initializer_path,
            "routingCompletion": routing_completion_path,
            "preTargetHceCompletion": hce_completion_path,
        }
        plan = {
            "bindings": {"staticHceExecutable": identities["executable"]},
            "dependencies": {"verifierRunner": {"actual": identities["runner"]}},
        }
        launch = {
            name: {"identity": identity}
            for name, identity in {
                "verifierExecutable": identities["executable"],
                "verifierRunner": identities["runner"],
                "verifierOptions": identities["options"],
                "capsule": identities["capsule"],
                "initializerManifest": identities["initializerManifest"],
                "routingCompletion": pipeline._identity(routing_completion_path),
                "preTargetHceCompletion": identities["hceCompletion"],
            }.items()
        }
        children = pipeline.PRODUCTION_INVENTORY_CONTRACT["totalChildren"]
        receipt = {
            "schemaVersion": 1,
            "kind": pipeline.VERIFICATION_KIND,
            "profileId": pipeline.PROFILE_ID,
            "status": "passed-fresh-semantic-replay",
            "capsule": identities["capsule"],
            "verifierExecutable": identities["executable"],
            "verifierRunner": identities["runner"],
            "verifierOptions": identities["options"],
            "initializerAuthority": {
                "manifest": identities["initializerManifest"],
                "selectionMode": "deterministic-fallback", "selectedCatalogIndex": None,
                "selectedModel": identities["initializerModel"],
                "catalogSourceIds": ["G5", "G2-K2"], "firstEligibleSelected": True,
                "selectionSemanticsVerified": True,
                "selectedPromotionHealthPassed": None,
                "sourceClosureSemanticsVerified": True,
                "fallbackProtocolReplayed": True, "g6TargetRowsDecoded": 0,
                "resultInformationRead": False,
            },
            "terminalAuthority": {
                "lineage": identities["terminalLineage"], "routedChildren": children,
                "terminalChildrenExcludedBeforeRouting": 1, "unclassifiedChildren": 0,
                "errorTextAcceptedAsTerminal": False, "rulesSemanticsReplayed": True,
                "completionSemanticsVerified": True,
            },
            "priorForbiddenAuthority": {
                "catalogs": [identities["priorG3"], identities["priorG45"]],
                "registry": identities["priorRegistry"],
                "requiredSourceIds": ["G3", "G4", "G5"], "catalogPositions": 100,
                "manifestsSemanticallyReplayed": True, "exactPositionOverlaps": 0,
                "conservativeSignatureOverlaps": 0, "sourceArtifactOverlaps": 0,
            },
            "componentAuthority": {
                "componentMap": identities["componentMap"],
                "roots": pipeline.PRODUCTION_INVENTORY_CONTRACT["totalRoots"],
                "components": 2048, "wholeComponentSplits": True,
                "semanticsReplayed": True,
            },
            "staticHceAuthority": {
                "claim": identities["hceClaim"], "completion": identities["hceCompletion"],
                "teacherClaim": identities["teacherClaim"], "prelabelSeal": identities["prelabel"],
                "targetFreeRouting": identities["routing"], "engine": identities["hceEngine"],
                "runner": identities["hceRunner"], "options": identities["hceOptions"],
                "transcript": identities["hceTranscript"], "inputOrderSha256": "a" * 64,
                "rows": children, "perspective": pipeline.STATIC_HCE_PERSPECTIVE,
                "freshReplayMatches": True, "completedBeforeTeacherClaim": True,
                "semanticsReplayed": True,
            },
            "teacherLedgerAuthority": {
                "claim": identities["teacherClaim"], "attemptLedger": identities["teacherLedger"],
                "attemptLedgerCompletion": identities["teacherLedgerCompletion"],
                "completion": identities["teacherCompletion"], "budgets": budgets,
                "routedChildren": children, "attemptRecords": children,
                "successfulChildren": children, "rejectedChildren": 0,
                "unresolvedChildren": 0, "semanticsReplayed": True,
            },
            "projectionAuthority": {
                "plannedProducer": identities["projectionProducer"],
                "plannedCorpusPath": identities["projectedCorpus"]["path"],
                "plannedManifestPath": identities["labelManifest"]["path"],
                "actualProducer": identities["projectionProducer"],
                "actualCorpus": identities["projectedCorpus"],
                "actualManifest": identities["labelManifest"],
                "producerMatches": True, "pathsMatch": True, "semanticsReplayed": True,
            },
            "resultInformationRead": False,
        }
        return receipt, plan, paths, launch

    def test_minimal_three_field_verifier_receipt_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "field inventory changed"):
            pipeline._validate_fresh_verifier_receipt(
                {
                    "kind": pipeline.VERIFICATION_KIND,
                    "status": "passed-fresh-semantic-replay",
                    "resultInformationRead": False,
                },
                {}, {}, {},
            )

    def test_complete_receipt_is_accepted_and_hostile_crosslink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            receipt, plan, paths, launch = self._fixture(Path(directory))
            accepted = pipeline._validate_fresh_verifier_receipt(
                receipt, plan, paths, launch
            )
            self.assertEqual(accepted, receipt)
            hostile = json.loads(json.dumps(receipt))
            hostile["projectionAuthority"]["actualManifest"] = hostile["projectionAuthority"]["actualCorpus"]
            with self.assertRaisesRegex(ValueError, "projection authority changed"):
                pipeline._validate_fresh_verifier_receipt(
                    hostile, plan, paths, launch
                )


if __name__ == "__main__":
    unittest.main()
