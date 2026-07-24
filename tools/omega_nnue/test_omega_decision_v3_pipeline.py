#!/usr/bin/env python3
"""Focused tests for the immutable Omega decision-v3 pipeline."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
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


class SyntheticPriorCatalogAuthority:
    """Target-opaque semantic replay stand-in for synthetic path fixtures."""

    def __init__(self, events: list[tuple[str, bool, str]] | None = None) -> None:
        self.events = events

    def verify_catalog_groups(
        self,
        groups,
        *,
        full_replay: bool,
        plan_created_utc: str,
    ) -> list[dict]:
        if self.events is not None:
            self.events.append(("prior", full_replay, plan_created_utc))
        return [
            {
                "coveredSourceIds": list(group["coveredSourceIds"]),
                "manifest": dict(group["manifest"]),
            }
            for group in groups
        ]


class ExternalTimestampTests(unittest.TestCase):
    def test_external_source_chronology_requires_canonical_utc(self) -> None:
        for value in (
            "2026-07-24T08:00:00Z",
            "2026-07-24T08:00:00.000000Z",
            "2026-07-24T08:00:00.123456Z",
        ):
            pipeline._parse_external_timestamp(value, "external authority")
        for value in (
            "2026-07-24T08:00:00.1Z",
            "2026-07-24T08:00:00.1234560Z",
            "2026-07-24T08:00:00+00:00",
        ):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "canonical UTC"
            ):
                pipeline._parse_external_timestamp(value, "external authority")

    def test_pipeline_rejects_hardlinked_cli_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "pipeline-original.py"
            alias = root / "pipeline-hardlink.py"
            original.write_bytes(Path(pipeline.__file__).read_bytes())
            try:
                os.link(original, alias)
            except OSError as error:
                self.skipTest(f"hardlinks unavailable: {error}")
            completed = subprocess.run(
                [sys.executable, "-I", "-B", str(alias), "status", "absent.json"],
                cwd=root,
                env={},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn(b"private regular file", completed.stderr)


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
                "prior-g3g4.json", "prior-g5.json",
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
                {"coveredSourceIds": ["G3", "G4"], "manifest": str(self.files["prior-g3g4.json"])},
                {"coveredSourceIds": ["G5"], "manifest": str(self.files["prior-g5.json"])},
            ],
            "teacherEngine": str(self.files["teacher.exe"]),
            "staticHceExecutable": str(Path(sys.executable).resolve()),
        }
        _write_json(self.inputs, self.inputs_document)
        self.plan = (self.authority / pipeline.CANONICAL_RELATIVE_PATHS["plan"]).resolve()

    def publish(self) -> dict:
        return pipeline.publish_plan(self.inputs, self.plan, created_utc=TIME)


class ExactPriorCatalogLoaderTests(unittest.TestCase):
    def test_final_pin_executes_only_under_the_private_exact_loader(self) -> None:
        authority = pipeline._load_exact_reviewed("priorCatalog")
        self.assertTrue(callable(authority.verify_catalog_groups))
        self.assertTrue(
            authority.__name__.startswith("_omega_decision_v3_pipeline_priorCatalog_")
        )
        self.assertIsNot(
            sys.modules.get("omega_decision_v3_prior_catalog"), authority
        )
        self.assertEqual(
            pipeline._identity(Path(authority.__file__)),
            {
                "path": str(
                    (
                        pipeline._tool_dir()
                        / "omega_decision_v3_prior_catalog.py"
                    ).resolve()
                ),
                "bytes": 56_580,
                "sha256": "c4eecaa481dbc1f4694fcee7dc4ec5954c7a23fb2148c3e155a55c409fe5f870",
            },
        )


class InitializerPublisherTests(unittest.TestCase):
    @staticmethod
    def _fixture(root: Path, *, promoted: bool) -> tuple[dict, dict, list[dict]]:
        root = root.resolve()
        authority = root / "authority"
        paths = {
            name: (authority / relative).resolve()
            for name, relative in pipeline.CANONICAL_RELATIVE_PATHS.items()
        }
        external = root / "external"
        external.mkdir(parents=True)

        def artifact(name: str, payload: bytes) -> dict:
            path = (external / name).resolve()
            path.write_bytes(payload)
            return pipeline._identity(path)

        g5_selection = artifact("g5-selection.json", b"{}\n")
        g5_closure = artifact("g5-closure.json", b"{}\n")
        g2_selection = artifact("g2-selection.json", b"{}\n")
        g2_closure = artifact("g2-closure.json", b"{}\n")
        g5_verifier = artifact("g5-verifier.py", b"# g5\n")
        g2_verifier = artifact("g2-verifier.py", b"# g2\n")
        source_model = artifact("g5.nnue", b"PROMOTED-G5-NETWORK\n")
        reports = [
            {
                "sourceId": "G5",
                "promotionStatus": "promoted" if promoted else "failed",
                "selectionSeal": g5_selection,
                "closure": g5_closure,
                "rawModel": source_model if promoted else None,
                "healthPassed": promoted,
                "sourceVerifier": g5_verifier,
                "unavailabilityEvidence": None,
                "resultInformationRead": False,
            },
            {
                "sourceId": "G2-K2",
                "promotionStatus": "unavailable",
                "selectionSeal": g2_selection,
                "closure": g2_closure,
                "rawModel": None,
                "healthPassed": False,
                "sourceVerifier": g2_verifier,
                "unavailabilityEvidence": {"authenticated": True},
                "resultInformationRead": False,
            },
        ]
        plan = {
            "createdUtc": "2026-07-24T07:00:00.000000Z",
            "paths": {name: str(path) for name, path in paths.items()},
            "dependencies": {},
            "bindings": {
                "staticHceExecutable": pipeline._identity(
                    Path(sys.executable).resolve()
                )
            },
        }
        return plan, paths, reports

    def _publish(
        self,
        root: Path,
        *,
        promoted: bool = False,
        validator_transform=None,
        post_fresh_hook=None,
        prewrite_hook=None,
        post_owned_write_hook=None,
    ):
        plan, paths, reports = self._fixture(root, promoted=promoted)
        calls: list[str] = []
        fallback = b"EXACT-DETERMINISTIC-FALLBACK\n"
        protocol = {
            "architecture": "king-state-v6-move-decision-initializer-v1",
            "seed": 2026072400,
            "prng": "numpy.random.default_rng-PCG64",
        }

        class FakeTrainer:
            @staticmethod
            def _verify_initializer_manifest(path, model):
                calls.append("trainer-verify")
                document = json.loads(Path(path).read_text(encoding="utf-8"))
                return (
                    validator_transform(document)
                    if validator_transform is not None
                    else document
                )

        class FakeRouting:
            @staticmethod
            def verify_completion(path):
                calls.append("routing")

        class FakeTerminal:
            @staticmethod
            def verify_terminal_lineage(path):
                calls.append("terminal")

        fake_verifier = types.SimpleNamespace(
            INITIALIZER_KIND="omega-nnue-king-state-v6-initializer-manifest",
            INITIALIZER_SELECTION_KIND=(
                "omega-decision-v3-pre-g6-initializer-selection"
            ),
            INITIALIZER_CLOSURE_KIND=(
                "omega-decision-v3-pre-g6-initializer-closure"
            ),
            FALLBACK_PROTOCOL=protocol,
            _verify_initializer_health=lambda model: calls.append("health"),
        )
        fake_generator = types.SimpleNamespace(
            fallback_bytes=lambda: fallback,
            _fallback_description=lambda payload: {
                "profileId": pipeline.PROFILE_ID,
                "architectureId": protocol["architecture"],
                "seed": protocol["seed"],
                "prng": protocol["prng"],
                "gameResultsRead": False,
                "targetRowsDecoded": 0,
            },
        )

        def exact(key: str):
            if key == "verifierImplementation":
                return fake_verifier
            if key == "initializerGenerator":
                return fake_generator
            raise AssertionError(key)

        def fresh(paths_value):
            calls.append("fresh-verifier")
            document = json.loads(
                paths_value["initializerManifest"].read_text(encoding="utf-8")
            )
            if post_fresh_hook is not None:
                post_fresh_hook(paths_value)
            return (
                validator_transform(document)
                if validator_transform is not None
                else document
            )

        def chronology(*args, **kwargs):
            if prewrite_hook is not None:
                prewrite_hook(paths)

        original_owned_write = pipeline._exclusive_bytes_owned

        def owned_write(path, payload):
            result = original_owned_write(path, payload)
            if post_owned_write_hook is not None:
                post_owned_write_hook(Path(path), payload)
            return result

        with mock.patch.object(
            pipeline, "verify_plan", return_value=plan
        ), mock.patch.object(
            pipeline, "_require_dependencies"
        ), mock.patch.object(
            pipeline,
            "_import_authorities",
            return_value=(FakeTrainer, FakeRouting, None, FakeTerminal),
        ), mock.patch.object(
            pipeline, "_require_routing_inventory"
        ), mock.patch.object(
            pipeline, "_initializer_source_reports", return_value=reports
        ) as source_replay, mock.patch.object(
            pipeline, "_require_after", side_effect=chronology
        ) as chronology_mock, mock.patch.object(
            pipeline, "_load_exact_reviewed", side_effect=exact
        ), mock.patch.object(
            pipeline, "_verify_initializer_source_authority", side_effect=fresh
        ), mock.patch.object(
            pipeline, "_exclusive_bytes_owned", side_effect=owned_write
        ):
            result = pipeline.publish_initializer(
                root / "plan.json", created_utc=TIME
            )
        return (
            result, paths, reports, fallback, calls, source_replay,
            chronology_mock,
        )

    def test_fallback_publishes_exactly_four_canonical_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result, paths, reports, fallback, calls, source, chronology = (
                self._publish(Path(directory))
            )
            initializer_directory = paths["initializerModel"].parent
            self.assertEqual(
                {path.name for path in initializer_directory.iterdir()},
                {
                    "initializer.selection.json",
                    "initializer.closure.json",
                    "initializer.nnue",
                    "initializer.manifest.json",
                },
            )
            self.assertEqual(paths["initializerModel"].read_bytes(), fallback)
            closure = json.loads(
                paths["initializerClosure"].read_text(encoding="utf-8")
            )
            manifest = json.loads(
                paths["initializerManifest"].read_text(encoding="utf-8")
            )
            self.assertEqual(closure["sourceReports"], reports)
            self.assertEqual(
                manifest["sourceClosure"],
                pipeline._identity(paths["initializerClosure"]),
            )
            self.assertEqual(result["selectionMode"], "deterministic-fallback")
            self.assertEqual(result["g6TargetRowsDecoded"], 0)
            self.assertIs(result["resultInformationRead"], False)
            for result_field, path_field in (
                ("selection", "initializerSelection"),
                ("closure", "initializerClosure"),
                ("model", "initializerModel"),
                ("manifest", "initializerManifest"),
            ):
                self.assertEqual(result[result_field], pipeline._identity(paths[path_field]))
            self.assertEqual(calls, ["routing", "terminal", "trainer-verify", "fresh-verifier"])
            source.assert_called_once()
            chronology.assert_called_once()

    def test_validator_returns_must_equal_the_in_memory_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def forge(document):
                return {**document, "forgedValidatorField": True}

            with self.assertRaisesRegex(
                ValueError, "differs from publication"
            ):
                self._publish(
                    Path(directory), validator_transform=forge
                )

    def test_post_validation_recheck_rejects_every_canonical_path_replacement(
        self,
    ) -> None:
        for role in (
            "initializerSelection",
            "initializerClosure",
            "initializerModel",
            "initializerManifest",
        ):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as directory:
                def replace(paths_value, role_value=role):
                    target = paths_value[role_value]
                    target.unlink()
                    target.write_bytes(
                        b"hostile replacement\n"
                        if role_value == "initializerModel"
                        else pipeline._canonical_json({"hostile": True})
                    )

                with self.assertRaisesRegex(
                    ValueError, "changed during fresh validation"
                ):
                    self._publish(
                        Path(directory), post_fresh_hook=replace
                    )

    def test_o_excl_ownership_rejects_identical_bytes_on_a_new_inode(self) -> None:
        for role in (
            "initializerSelection",
            "initializerClosure",
            "initializerModel",
            "initializerManifest",
        ):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = (
                    root
                    / "authority"
                    / pipeline.CANONICAL_RELATIVE_PATHS[role]
                ).resolve()
                swapped = False

                def replace_after_owned_write(path, payload):
                    nonlocal swapped
                    if swapped or path != target:
                        return
                    displaced = root / f"displaced-{role}"
                    path.replace(displaced)
                    path.write_bytes(payload)
                    swapped = True

                with self.assertRaisesRegex(
                    ValueError, "owned publication changed before return"
                ):
                    self._publish(
                        root,
                        post_owned_write_hook=replace_after_owned_write,
                    )
                self.assertTrue(swapped)
                self.assertEqual(target.read_bytes(), (root / f"displaced-{role}").read_bytes())

    def test_fifth_file_injection_is_rejected_prewrite_and_at_final_boundary(
        self,
    ) -> None:
        for phase in ("prewrite", "final"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                def inject(paths_value):
                    parent = paths_value["initializerModel"].parent
                    parent.mkdir(parents=True, exist_ok=True)
                    (parent / "fifth-file.attack").write_bytes(b"attack\n")

                kwargs = (
                    {"prewrite_hook": inject}
                    if phase == "prewrite"
                    else {"post_fresh_hook": inject}
                )
                with self.assertRaisesRegex(
                    FileExistsError, "four-file namespace"
                ):
                    self._publish(Path(directory), **kwargs)

    def test_runtime_must_match_plan_and_use_isolated_no_bytecode_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan, _, _ = self._fixture(root, promoted=False)
            plan["bindings"]["staticHceExecutable"] = {
                **plan["bindings"]["staticHceExecutable"],
                "sha256": "0" * 64,
            }
            with mock.patch.object(
                pipeline, "verify_plan", return_value=plan
            ), mock.patch.object(pipeline, "_require_dependencies"):
                with self.assertRaisesRegex(ValueError, "runtime differs"):
                    pipeline.publish_initializer(root / "plan", created_utc=TIME)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan, _, _ = self._fixture(root, promoted=False)
            flags = types.SimpleNamespace(isolated=0, dont_write_bytecode=1)
            with mock.patch.object(
                pipeline, "verify_plan", return_value=plan
            ), mock.patch.object(
                pipeline, "_require_dependencies"
            ), mock.patch.object(pipeline.sys, "flags", flags):
                with self.assertRaisesRegex(RuntimeError, "-I -B"):
                    pipeline.publish_initializer(root / "plan", created_utc=TIME)

    def test_promoted_model_is_exactly_copied_but_source_identity_is_retained(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result, paths, reports, _, calls, _, _ = self._publish(
                Path(directory), promoted=True
            )
            selection = json.loads(
                paths["initializerSelection"].read_text(encoding="utf-8")
            )
            source = reports[0]["rawModel"]
            canonical = pipeline._identity(paths["initializerModel"])
            self.assertNotEqual(source["path"], canonical["path"])
            self.assertEqual(source["bytes"], canonical["bytes"])
            self.assertEqual(source["sha256"], canonical["sha256"])
            self.assertEqual(selection["orderedCatalog"][0]["model"], source)
            self.assertEqual(selection["selectedModel"], canonical)
            self.assertEqual(result["selectionMode"], "promoted-prior")
            self.assertIn("health", calls)

    def test_existing_output_rejects_before_any_source_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan, paths, _ = self._fixture(root, promoted=False)
            paths["initializerModel"].parent.mkdir(parents=True)
            paths["initializerModel"].write_bytes(b"occupied")
            with mock.patch.object(
                pipeline, "verify_plan", return_value=plan
            ), mock.patch.object(
                pipeline, "_require_dependencies"
            ), mock.patch.object(
                pipeline, "_initializer_source_reports"
            ) as source:
                with self.assertRaisesRegex(FileExistsError, "no-clobber"):
                    pipeline.publish_initializer(
                        root / "plan.json", created_utc=TIME
                    )
            source.assert_not_called()

    def test_source_replay_failure_publishes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan, paths, _ = self._fixture(root, promoted=False)
            fake = types.SimpleNamespace()
            with mock.patch.object(
                pipeline, "verify_plan", return_value=plan
            ), mock.patch.object(
                pipeline, "_require_dependencies"
            ), mock.patch.object(
                pipeline,
                "_import_authorities",
                return_value=(object(), types.SimpleNamespace(verify_completion=lambda path: None), None,
                              types.SimpleNamespace(verify_terminal_lineage=lambda path: None)),
            ), mock.patch.object(
                pipeline, "_require_routing_inventory"
            ), mock.patch.object(
                pipeline, "_load_exact_reviewed", return_value=fake
            ), mock.patch.object(
                pipeline,
                "_initializer_source_reports",
                side_effect=ValueError("injected source replay failure"),
            ):
                with self.assertRaisesRegex(ValueError, "source replay failure"):
                    pipeline.publish_initializer(root / "plan.json", created_utc=TIME)
            self.assertFalse(paths["initializerModel"].parent.exists())

    def test_nonterminal_g5_cannot_authorize_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan, paths, reports = self._fixture(root, promoted=False)
            reports[0]["promotionStatus"] = "pending"
            fake_verifier = types.SimpleNamespace(
                FALLBACK_PROTOCOL={
                    "architecture": "king-state-v6-move-decision-initializer-v1",
                    "seed": 2026072400,
                    "prng": "numpy.random.default_rng-PCG64",
                }
            )
            with mock.patch.object(
                pipeline, "verify_plan", return_value=plan
            ), mock.patch.object(
                pipeline, "_require_dependencies"
            ), mock.patch.object(
                pipeline,
                "_import_authorities",
                return_value=(object(), types.SimpleNamespace(verify_completion=lambda path: None), None,
                              types.SimpleNamespace(verify_terminal_lineage=lambda path: None)),
            ), mock.patch.object(
                pipeline, "_require_routing_inventory"
            ), mock.patch.object(
                pipeline, "_load_exact_reviewed", return_value=fake_verifier
            ), mock.patch.object(
                pipeline, "_initializer_source_reports", return_value=reports
            ), mock.patch.object(
                pipeline, "_require_after"
            ):
                with self.assertRaisesRegex(ValueError, "not source-authorized"):
                    pipeline.publish_initializer(root / "plan.json", created_utc=TIME)
            self.assertFalse(paths["initializerModel"].parent.exists())

    def test_chronology_failure_precedes_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan, paths, reports = self._fixture(root, promoted=True)
            fake_verifier = types.SimpleNamespace(
                _verify_initializer_health=lambda model: None
            )
            with mock.patch.object(
                pipeline, "verify_plan", return_value=plan
            ), mock.patch.object(
                pipeline, "_require_dependencies"
            ), mock.patch.object(
                pipeline,
                "_import_authorities",
                return_value=(object(), types.SimpleNamespace(verify_completion=lambda path: None), None,
                              types.SimpleNamespace(verify_terminal_lineage=lambda path: None)),
            ), mock.patch.object(
                pipeline, "_require_routing_inventory"
            ), mock.patch.object(
                pipeline, "_load_exact_reviewed", return_value=fake_verifier
            ), mock.patch.object(
                pipeline, "_initializer_source_reports", return_value=reports
            ), mock.patch.object(
                pipeline,
                "_require_after",
                side_effect=ValueError("injected chronology failure"),
            ):
                with self.assertRaisesRegex(ValueError, "chronology failure"):
                    pipeline.publish_initializer(root / "plan.json", created_utc=TIME)
            self.assertFalse(paths["initializerModel"].parent.exists())


class PipelinePlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prior_events: list[tuple[str, bool, str]] = []
        authority = SyntheticPriorCatalogAuthority(self.prior_events)
        real_loader = pipeline._load_exact_reviewed

        def load_exact(key: str):
            if key == "priorCatalog":
                return authority
            return real_loader(key)

        self.exact_loader = mock.patch.object(
            pipeline, "_load_exact_reviewed", side_effect=load_exact
        )
        self.exact_loader.start()

    def tearDown(self) -> None:
        self.exact_loader.stop()

    def test_capsule_path_matches_generation6_trainer_contract(self) -> None:
        self.assertEqual(
            pipeline.CANONICAL_RELATIVE_PATHS["capsule"],
            "capsule.closure.json",
        )

    def test_pretarget_and_capsule_replays_reject_fifth_initializer_file(
        self,
    ) -> None:
        for stage in ("pretarget", "capsule"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                fixture = PlanFixture(Path(directory))
                plan = fixture.publish()
                paths = pipeline._paths(plan)
                initializer_directory = paths["initializerModel"].parent
                initializer_directory.mkdir(parents=True)
                for role in (
                    "initializerSelection",
                    "initializerClosure",
                    "initializerModel",
                    "initializerManifest",
                ):
                    paths[role].write_bytes(b"fixture\n")
                (initializer_directory / "fifth-file.attack").write_bytes(
                    b"attack\n"
                )
                if stage == "pretarget":
                    action = lambda: pipeline.claim_stage(
                        fixture.plan, "pretarget", created_utc=TIME
                    )
                else:
                    action = lambda: pipeline.finalize_stage(
                        fixture.plan, "capsule", created_utc=TIME
                    )
                with mock.patch.object(
                    pipeline, "verify_plan", return_value=plan
                ), mock.patch.object(
                    pipeline, "_require_dependencies"
                ), mock.patch.object(
                    pipeline, "_verify_prior_catalog_groups"
                ), mock.patch.object(
                    pipeline, "_import_authorities"
                ) as imported:
                    with self.assertRaisesRegex(
                        FileExistsError, "four-file namespace"
                    ):
                        action()
                imported.assert_not_called()

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
            initializer = next(
                item for item in plan["runbook"] if item["stage"] == "initializer"
            )
            self.assertEqual(initializer["action"], "pipeline-command")
            self.assertIn("publish-initializer", initializer["argv"])

    def test_full_semantic_replay_precedes_plan_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PlanFixture(Path(directory))
            events: list[tuple[str, bool | None, str]] = []
            authority = SyntheticPriorCatalogAuthority(events)
            real_exclusive_json = pipeline._exclusive_json

            def publish(path: Path, value: object) -> None:
                events.append(("publish", None, str(path)))
                real_exclusive_json(path, value)

            with mock.patch.object(
                pipeline, "_load_exact_reviewed", return_value=authority
            ), mock.patch.object(
                pipeline, "_exclusive_json", side_effect=publish
            ):
                fixture.publish()
            full = events.index(("prior", True, TIME))
            publication = next(
                index for index, event in enumerate(events) if event[0] == "publish"
            )
            self.assertLess(full, publication)

    def test_plan_publication_is_fail_closed_on_semantic_replay_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PlanFixture(Path(directory))
            failing = types.SimpleNamespace(
                verify_catalog_groups=mock.Mock(
                    side_effect=ValueError("injected prior semantic failure")
                )
            )
            with mock.patch.object(
                pipeline, "_load_exact_reviewed", return_value=failing
            ):
                with self.assertRaisesRegex(ValueError, "prior semantic failure"):
                    fixture.publish()
            self.assertFalse(fixture.plan.exists())
            self.assertEqual(list(fixture.authority.rglob("*")), [])

    def test_plan_requires_the_exact_private_prior_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PlanFixture(Path(directory))
            with mock.patch.object(
                pipeline,
                "_load_exact_reviewed",
                side_effect=RuntimeError("injected exact dependency failure"),
            ) as exact_loader:
                with self.assertRaisesRegex(RuntimeError, "exact dependency failure"):
                    fixture.publish()
            exact_loader.assert_called_once_with("priorCatalog")
            self.assertFalse(fixture.plan.exists())

    def test_plan_requires_a_final_matching_prior_dependency_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PlanFixture(Path(directory))
            dependencies = pipeline._dependency_records()
            dependencies["priorCatalog"] = {
                **dependencies["priorCatalog"],
                "pinFinalized": False,
                "matches": False,
            }
            with mock.patch.object(
                pipeline, "_dependency_records", return_value=dependencies
            ), mock.patch.object(
                pipeline, "_load_exact_reviewed"
            ) as exact_loader:
                with self.assertRaisesRegex(RuntimeError, "not final and matching"):
                    fixture.publish()
            exact_loader.assert_not_called()
            self.assertFalse(fixture.plan.exists())

    def test_prior_catalog_pin_matches_the_final_reviewed_file(self) -> None:
        filename, expected_bytes, expected_sha256 = pipeline.REVIEWED_PINS[
            "priorCatalog"
        ]
        self.assertEqual(filename, "omega_decision_v3_prior_catalog.py")
        self.assertEqual(expected_bytes, 56_580)
        self.assertEqual(
            expected_sha256,
            "c4eecaa481dbc1f4694fcee7dc4ec5954c7a23fb2148c3e155a55c409fe5f870",
        )
        self.assertEqual(
            pipeline._identity(pipeline._tool_dir() / filename),
            {
                "path": str((pipeline._tool_dir() / filename).resolve()),
                "bytes": expected_bytes,
                "sha256": expected_sha256,
            },
        )

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
            with self.assertRaisesRegex(ValueError, "exact semantic authorities"):
                fixture.publish()

    def test_plan_rejects_the_misleading_g3_then_g4g5_grouping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PlanFixture(Path(directory))
            fixture.inputs_document["priorForbiddenGroups"][0]["coveredSourceIds"] = ["G3"]
            fixture.inputs_document["priorForbiddenGroups"][1]["coveredSourceIds"] = ["G4", "G5"]
            _write_json(fixture.inputs, fixture.inputs_document)
            with self.assertRaisesRegex(ValueError, "exact semantic authorities"):
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

    def test_verify_plan_rechecks_prior_lineage_without_full_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PlanFixture(Path(directory))
            fixture.publish()
            self.prior_events.clear()
            pipeline.verify_plan(fixture.plan)
            self.assertEqual(self.prior_events, [("prior", False, TIME)])

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
                    347_813,
                    "d5a27adba652b3dd5669fa530f92d23a262b0752faa00120905e87f15cbdd19b",
                ),
                "initializerGenerator": (
                    19_564,
                    "38d81f665d0bccb4939e3a1707b7dbfbe4c9c29795e0699f51d92bf30af94be0",
                ),
                "verifierImplementation": (
                    181_433,
                    "a45f9b771d4f7fab3c6b7f7c9a14a54ffaf3e192329929ca6c87c8420c005dfd",
                ),
                "verifierRunner": (
                    5_356,
                    "4d7ffc2becade214f8db6f5d171809ed423d3d879754de84abcc0144d9e9c232",
                ),
                "priorCatalog": (
                    56_580,
                    "c4eecaa481dbc1f4694fcee7dc4ec5954c7a23fb2148c3e155a55c409fe5f870",
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
            pipeline._require_dependencies(plan, tuple(expected))
            self.assertTrue(
                all(
                    record["pinFinalized"] is True and record["matches"] is True
                    for record in plan["dependencies"].values()
                )
            )
            prior_filename, prior_size, prior_sha256 = pipeline.REVIEWED_PINS[
                "priorCatalog"
            ]
            self.assertEqual(
                prior_filename, "omega_decision_v3_prior_catalog.py"
            )
            prior = plan["dependencies"]["priorCatalog"]
            self.assertEqual(prior_size, 56_580)
            self.assertEqual(
                prior_sha256,
                "c4eecaa481dbc1f4694fcee7dc4ec5954c7a23fb2148c3e155a55c409fe5f870",
            )
            self.assertIs(prior["pinFinalized"], True)
            self.assertIs(prior["matches"], True)
            self.assertEqual(prior["expectedBytes"], prior_size)
            self.assertEqual(prior["expectedSha256"], prior_sha256)

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

    def test_full_replay_failure_precedes_every_pretarget_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PlanFixture(Path(directory))
            plan = fixture.publish()
            with mock.patch.object(
                pipeline, "verify_plan", return_value=plan
            ), mock.patch.object(
                pipeline, "_require_dependencies"
            ), mock.patch.object(
                pipeline,
                "_verify_prior_catalog_groups",
                side_effect=ValueError("injected pretarget catalog failure"),
            ) as semantic, mock.patch.object(
                pipeline, "_import_authorities"
            ) as imports:
                with self.assertRaisesRegex(ValueError, "pretarget catalog failure"):
                    pipeline.claim_stage(fixture.plan, "pretarget", created_utc=TIME)
            semantic.assert_called_once_with(
                plan["priorForbiddenGroups"],
                plan_created_utc=TIME,
                full_replay=True,
            )
            imports.assert_not_called()
            for name in (
                "priorForbiddenRegistry", "prelabelSeal", "staticHceOptions",
                "preTargetHceClaim",
            ):
                self.assertFalse(Path(plan["paths"][name]).exists())

    def test_full_replay_failure_precedes_hce_and_teacher_target_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PlanFixture(Path(directory))
            plan = fixture.publish()
            for action in (
                lambda: pipeline.materialize_hce(fixture.plan),
                lambda: pipeline.claim_stage(
                    fixture.plan, "teacher", created_utc=TIME
                ),
            ):
                with self.subTest(action=action):
                    with mock.patch.object(
                        pipeline, "verify_plan", return_value=plan
                    ), mock.patch.object(
                        pipeline, "_require_dependencies"
                    ), mock.patch.object(
                        pipeline,
                        "_verify_prior_catalog_groups",
                        side_effect=ValueError("injected target-work catalog failure"),
                    ) as semantic, mock.patch.object(
                        pipeline, "_import_authorities"
                    ) as imports:
                        with self.assertRaisesRegex(
                            ValueError, "target-work catalog failure"
                        ):
                            action()
                    semantic.assert_called_once_with(
                        plan["priorForbiddenGroups"],
                        plan_created_utc=TIME,
                        full_replay=True,
                    )
                    imports.assert_not_called()
            self.assertFalse(Path(plan["paths"]["staticHceTranscript"]).exists())
            self.assertFalse(Path(plan["paths"]["teacherOptions"]).exists())
            self.assertFalse(Path(plan["paths"]["teacherClaim"]).exists())

    def test_capsule_finalization_requires_a_fresh_full_prior_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PlanFixture(Path(directory))
            plan = fixture.publish()
            with mock.patch.object(
                pipeline, "verify_plan", return_value=plan
            ), mock.patch.object(
                pipeline, "_require_dependencies"
            ), mock.patch.object(
                pipeline,
                "_verify_prior_catalog_groups",
                side_effect=ValueError("injected capsule catalog failure"),
            ) as semantic, mock.patch.object(
                pipeline, "_import_authorities"
            ) as imports:
                with self.assertRaisesRegex(ValueError, "capsule catalog failure"):
                    pipeline.finalize_stage(
                        fixture.plan, "capsule", created_utc=TIME
                    )
            semantic.assert_called_once_with(
                plan["priorForbiddenGroups"],
                plan_created_utc=TIME,
                full_replay=True,
            )
            imports.assert_not_called()
            self.assertFalse(Path(plan["paths"]["capsule"]).exists())

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
                "priorForbiddenGroups": [],
                "createdUtc": TIME,
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
                pipeline, "_verify_prior_catalog_groups"
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
            plan = {
                "paths": {name: str(path) for name, path in paths.items()},
                "priorForbiddenGroups": [],
                "createdUtc": TIME,
            }
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
                pipeline, "_verify_prior_catalog_groups"
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
            for role in (
                "initializerSelection",
                "initializerClosure",
                "initializerModel",
                "initializerManifest",
            ):
                paths[role].parent.mkdir(parents=True, exist_ok=True)
                paths[role].write_bytes(b"fixture\n")
            options = {"schemaVersion": 1, "kind": "test-verifier-options"}
            _write_json(paths["verifierOptions"], options)
            plan = {
                "paths": {name: str(path) for name, path in paths.items()},
                "bindings": {"staticHceExecutable": {"path": str(root / "python")}},
                "dependencies": {
                    "verifierRunner": {"actual": {"path": str(root / "runner")}},
                    "evaluatorRunner": {"actual": {"path": str(root / "evaluator")}},
                },
                "priorForbiddenGroups": [],
                "createdUtc": TIME,
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
                pipeline, "_verify_prior_catalog_groups"
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
