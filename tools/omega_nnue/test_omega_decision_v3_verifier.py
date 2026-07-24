#!/usr/bin/env python3
"""Focused semantic and hostile tests for the decision-v3 verifier."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().with_name("omega_decision_v3_verifier.py")
WRAPPER_PATH = MODULE_PATH.with_name("verify_omega_decision_v3_upstream.py")
SPEC = importlib.util.spec_from_file_location("omega_decision_v3_verifier_tested", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
verifier = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verifier
SPEC.loader.exec_module(verifier)


def _canonical(value: object) -> bytes:
    return verifier._canonical_json(value)


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(_canonical(value))


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_bytes(b"".join(_canonical(row) for row in rows))


def _timestamp(offset: int) -> str:
    base = datetime(2026, 7, 24, tzinfo=timezone.utc) + timedelta(seconds=offset)
    return base.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _install_source_report(
    root: Path, source_id: str, report: dict, name: str
) -> None:
    runner = root / f"{name}-source-verifier.py"
    encoded = json.dumps(
        report,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    runner.write_text(
        "import sys\n"
        f"assert sys.argv[1:] == [{source_id!r}]\n"
        f"print({encoded!r})\n",
        encoding="utf-8",
    )
    identity = verifier._identity(runner)
    verifier._TEST_INITIALIZER_SOURCE_RUNNERS[source_id] = (
        runner,
        identity["bytes"],
        identity["sha256"],
    )


def _synthetic_g2_unavailability_evidence(root: Path, name: str) -> dict:
    deep_freeze = root / f"{name}-deep-freeze.json"
    incident = root / f"{name}-generation2-incident.json"
    root_sampler = root / f"{name}-root-sampler.cs"
    omega_nnue = root / f"{name}-omega-nnue.py"
    deep_freeze.write_text("{}\n", encoding="utf-8")
    incident.write_text("{}\n", encoding="utf-8")
    root_sampler.write_text("// current root sampler\n", encoding="utf-8")
    omega_nnue.write_text("# current omega nnue\n", encoding="utf-8")
    frozen_root = verifier.G2_UNAVAILABLE_PROTOCOL["frozenDependencies"][
        "rootSamplerSource"
    ]
    frozen_omega = verifier.G2_UNAVAILABLE_PROTOCOL["frozenDependencies"][
        "omegaNnue"
    ]
    return {
        "protocol": dict(verifier.G2_UNAVAILABLE_PROTOCOL),
        "fullReplayRejected": True,
        "deepReplayRejected": True,
        "compatibilityPatchesApplied": False,
        "deepFreeze": verifier._identity(deep_freeze),
        "generation2Incident": verifier._identity(incident),
        "currentRootSamplerSource": verifier._identity(root_sampler),
        "frozenRootSamplerSource": {
            "path": str(root_sampler.resolve()),
            "bytes": frozen_root["bytes"],
            "sha256": frozen_root["sha256"],
        },
        "currentOmegaNnueModule": verifier._identity(omega_nnue),
        "frozenOmegaNnueModule": {
            "path": str(omega_nnue.resolve()),
            "bytes": frozen_omega["bytes"],
            "sha256": frozen_omega["sha256"],
        },
        "resultInformationRead": False,
    }


def _generate_fallback_payload(root: Path) -> bytes:
    fallback = root / "fallback.nnue"
    generator = MODULE_PATH.with_name("omega_decision_v3_initializer.py")
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(generator),
            "--write-fallback",
            str(fallback),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={},
        cwd=generator.parent,
        check=False,
        timeout=180,
    )
    if completed.returncode != 0 or completed.stderr:
        raise RuntimeError(
            "fallback fixture generation failed: "
            + completed.stderr.decode("utf-8", errors="replace")
        )
    return fallback.read_bytes()


def _route_rows() -> tuple[list[dict], list[dict]]:
    board = "5k4/10/10/10/10/4P5/10/10/10/4K5[-/-/-/-]"
    children = [
        {
            "childId": f"child-{index}",
            "normalizedChildOfen": f"{board} b - - 0 {index}",
        }
        for index in range(1, 5)
    ]
    routes = [
        {
            "schemaVersion": 1,
            "kind": verifier.ROUTING_KIND,
            "profileId": verifier.PROFILE_ID,
            "rootId": "root-1",
            "sourceRootId": "source-root-1",
            "sourceGroupId": "source-group-1",
            "phase": "endgame",
            "parentSideToMove": "w",
            "children": list(reversed(children)),
        }
    ]
    components = [
        {
            "schemaVersion": 1,
            "kind": verifier.COMPONENT_KIND,
            "profileId": verifier.PROFILE_ID,
            "rootId": "root-1",
            "leakageComponentId": "component-1",
            "split": "train",
            "sourceRootId": "source-root-1",
            "sourceGroupId": "source-group-1",
        }
    ]
    return routes, components


def _fixed_quota_inventories() -> tuple[dict, dict]:
    root_inventories = {}
    phase_side_inventories = {}
    for split in verifier.SPLITS:
        quota = verifier.ROUTING_QUOTAS_PER_PHASE_SIDE[split]
        roots = quota * len(verifier.CELLS)
        root_inventories[split] = {
            "roots": roots,
            "children": roots * verifier.CHILDREN_PER_ROOT,
            "sha256": "0" * 64,
        }
        phase_side_inventories[split] = {
            cell: {"roots": quota, "sha256": "1" * 64}
            for cell in verifier.CELLS
        }
    return root_inventories, phase_side_inventories


class VerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Mirror the production entry point: the prior-catalog authority must
        # establish the frozen G5 runtime contract before any test can load a
        # NumPy-bearing verifier dependency.
        cls.prior_catalog_dependency = verifier._load_pinned("priorCatalog")
        cls.fallback_temporary = tempfile.TemporaryDirectory(
            prefix="omega-decision-v3-fallback-fixture-"
        )
        cls.fallback_payload = _generate_fallback_payload(
            Path(cls.fallback_temporary.name)
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fallback_temporary.cleanup()

    def setUp(self) -> None:
        verifier._TEST_INITIALIZER_SOURCE_RUNNERS.clear()
        self.temporary = tempfile.TemporaryDirectory(
            prefix="omega-decision-v3-verifier-test-"
        )
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        verifier._TEST_INITIALIZER_SOURCE_RUNNERS.clear()
        self.temporary.cleanup()

    def _install_source_report(
        self, source_id: str, report: dict, name: str
    ) -> None:
        _install_source_report(self.root, source_id, report, name)

    def _database(self) -> tuple[verifier.AuthorityDatabase, int, int, str]:
        routes, components = _route_rows()
        routing = self.root / "routing.jsonl"
        component = self.root / "components.jsonl"
        _write_jsonl(routing, routes)
        _write_jsonl(component, components)
        database = verifier.AuthorityDatabase()
        roots, children, digest = verifier._parse_routes(
            routing, verifier._identity(routing), database
        )
        verifier._parse_components(
            component, verifier._identity(component), database, roots
        )
        return database, roots, children, digest

    def test_options_publisher_is_exact_and_no_clobber(self) -> None:
        output = self.root / "options.json"
        identity = verifier.publish_verifier_options(output)
        self.assertEqual(output.read_bytes(), _canonical(verifier.VERIFIER_OPTIONS))
        self.assertEqual(identity, verifier._identity(output))
        with self.assertRaises(FileExistsError):
            verifier.publish_verifier_options(output)

    def test_prior_catalog_uses_final_exact_private_authority(self) -> None:
        dependency = self.prior_catalog_dependency
        filename, expected_bytes, expected_sha256 = verifier.DEPENDENCY_PINS[
            "priorCatalog"
        ]
        self.assertEqual(filename, "omega_decision_v3_prior_catalog.py")
        self.assertEqual(dependency.identity["bytes"], expected_bytes)
        self.assertEqual(dependency.identity["sha256"], expected_sha256)
        self.assertEqual(
            dependency.module.__name__,
            verifier._PINNED_MODULE_PREFIX + "priorCatalog",
        )
        self.assertTrue(callable(dependency.module.verify_catalog_groups))
        self.assertIs(
            sys.modules[dependency.module.__name__], dependency.module
        )

    def test_prior_catalog_private_slot_substitution_fails_closed(self) -> None:
        dependency = self.prior_catalog_dependency
        private_name = dependency.module.__name__
        self.assertIs(sys.modules[private_name], dependency.module)
        sys.modules[private_name] = types.ModuleType("substituted-prior-catalog")
        try:
            with self.assertRaisesRegex(RuntimeError, "private module was substituted"):
                verifier._load_pinned("priorCatalog")
        finally:
            sys.modules[private_name] = dependency.module

    def test_contract_alignment_uses_exact_reviewed_sources(self) -> None:
        trainer, teacher = verifier._verify_contract_alignment()
        self.assertEqual(trainer.UPSTREAM_VERIFIER_OPTIONS, verifier.VERIFIER_OPTIONS)
        self.assertEqual(teacher.BUDGETS, verifier.TEACHER_BUDGETS)

    def test_verify_capsule_loads_prior_catalog_before_contract_alignment(self) -> None:
        events: list[str] = []

        def load(name: str) -> verifier.PinnedModule:
            events.append(f"load:{name}")
            return verifier.PinnedModule({}, types.ModuleType("prior-catalog-test"))

        def align() -> tuple[types.ModuleType, types.ModuleType]:
            events.append("contract-alignment")
            raise RuntimeError("stop after ordering boundary")

        with mock.patch.object(verifier, "_load_pinned", side_effect=load), mock.patch.object(
            verifier, "_verify_contract_alignment", side_effect=align
        ):
            with self.assertRaisesRegex(RuntimeError, "ordering boundary"):
                verifier.verify_capsule(
                    self.root / "capsule.json",
                    self.root / "options.json",
                    self.root / "initializer.json",
                )
        self.assertEqual(events, ["load:priorCatalog", "contract-alignment"])

    def test_pinned_loader_rejects_private_module_substitution(self) -> None:
        source = self.root / "slot-authority.py"
        source.write_text("VALUE = 17\n", encoding="utf-8")
        identity = verifier._identity(source)
        logical_name = "privateSlotTest"
        private_name = verifier._PINNED_MODULE_PREFIX + logical_name
        original_dependency_path = verifier._dependency_path
        previous_slot = sys.modules.get(private_name)
        previous_cache = verifier._PINNED_CACHE.pop(logical_name, None)
        verifier.DEPENDENCY_PINS[logical_name] = (
            source.name,
            identity["bytes"],
            identity["sha256"],
        )

        def dependency_path(filename: str) -> Path:
            if filename == source.name:
                return source
            return original_dependency_path(filename)

        try:
            with mock.patch.object(verifier, "_dependency_path", side_effect=dependency_path):
                sys.modules[private_name] = types.ModuleType("preloaded-attacker")
                with self.assertRaisesRegex(RuntimeError, "private module was preloaded"):
                    verifier._load_pinned(logical_name)

                del sys.modules[private_name]
                loaded = verifier._load_pinned(logical_name)
                self.assertEqual(loaded.module.VALUE, 17)
                sys.modules[private_name] = types.ModuleType("cached-attacker")
                with self.assertRaisesRegex(RuntimeError, "private module was substituted"):
                    verifier._load_pinned(logical_name)
        finally:
            verifier._PINNED_CACHE.pop(logical_name, None)
            verifier.DEPENDENCY_PINS.pop(logical_name, None)
            sys.modules.pop(private_name, None)
            if previous_cache is not None:
                verifier._PINNED_CACHE[logical_name] = previous_cache
            if previous_slot is not None:
                sys.modules[private_name] = previous_slot

    def test_swapped_self_consistent_registry_fails_before_routing_replay(self) -> None:
        producer = self.root / "registry-producer.py"
        producer.write_text("# registry producer\n", encoding="utf-8")
        g34_manifest = self.root / "g34.manifest.json"
        g5_manifest = self.root / "g5.manifest.json"
        _write_json(g34_manifest, {"authority": "G3+G4"})
        _write_json(g5_manifest, {"authority": "G5"})
        g34_identity = verifier._identity(g34_manifest)
        g5_identity = verifier._identity(g5_manifest)
        created_utc = _timestamp(1)
        registry_path = self.root / "prior-forbidden.registry.json"
        registry = {
            "schemaVersion": 1,
            "kind": verifier.FORBIDDEN_REGISTRY_KIND,
            "profileId": verifier.PROFILE_ID,
            "status": "frozen-complete-prior-source-registry",
            "createdUtc": created_utc,
            "requiredSourceIds": list(verifier.PRIOR_SOURCE_IDS),
            "catalogs": [
                {"coveredSourceIds": ["G5"], "manifest": g5_identity},
                {
                    "coveredSourceIds": ["G3", "G4"],
                    "manifest": g34_identity,
                },
            ],
            "producer": verifier._identity(producer),
            "targetFieldsDecodedAtSeal": 0,
            "resultInformationRead": False,
            "finalStageSeal": True,
        }
        _write_json(registry_path, registry)
        capsule = {
            # The capsule agrees with the forged registry, so only independent
            # authority replay can reject the swapped partition at this point.
            "priorForbiddenCatalogs": [g5_identity, g34_identity],
        }
        identities = {"priorForbiddenRegistry": verifier._identity(registry_path)}
        events: list[str] = []
        prior_catalog = self.prior_catalog_dependency.module
        real_verify_catalog_groups = prior_catalog.verify_catalog_groups
        routing = types.ModuleType("routing-authority-test")

        def verify_catalog_groups(
            groups: object,
            *,
            full_replay: bool,
            plan_created_utc: str,
        ) -> list[dict]:
            events.append("prior-catalog-full-replay")
            self.assertIs(full_replay, True)
            self.assertEqual(plan_created_utc, created_utc)
            self.assertEqual(
                [entry["coveredSourceIds"] for entry in groups],
                [["G5"], ["G3", "G4"]],
            )
            return real_verify_catalog_groups(
                groups,
                full_replay=full_replay,
                plan_created_utc=plan_created_utc,
            )

        def load_forbidden(_paths: object) -> None:
            events.append("routing-replay")
            raise AssertionError("routing replay ran before catalog authority rejection")

        routing._load_forbidden = load_forbidden
        real_load_pinned = verifier._load_pinned

        def load(name: str) -> verifier.PinnedModule:
            if name == "routing":
                return verifier.PinnedModule({}, routing)
            return real_load_pinned(name)

        with (
            verifier.AuthorityDatabase() as database,
            mock.patch.object(verifier, "_load_pinned", side_effect=load),
            mock.patch.object(
                prior_catalog,
                "verify_catalog_groups",
                side_effect=verify_catalog_groups,
            ),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "ordered as \\[G3,G4\\] existing v2 plus \\[G5\\]",
            ):
                verifier._verify_forbidden(capsule, identities, {}, database)
        self.assertEqual(events, ["prior-catalog-full-replay"])

    def test_canonical_wrapper_loads_exact_verifier_and_sets_runner(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "omega_decision_v3_wrapper_tested", WRAPPER_PATH
        )
        assert spec is not None and spec.loader is not None
        wrapper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wrapper)
        canonical_name = "_omega_decision_v3_canonical_verifier"
        sys.modules.pop(canonical_name, None)
        try:
            loaded, identity = wrapper._load_verifier()
            self.assertEqual(identity, verifier._identity(MODULE_PATH))
            self.assertEqual(
                loaded.VERIFIER_RUNNER_OVERRIDE, WRAPPER_PATH.resolve()
            )
            self.assertEqual(
                loaded._verifier_runner_path(), WRAPPER_PATH.resolve()
            )
        finally:
            sys.modules.pop(canonical_name, None)

    def test_canonical_wrapper_self_test_is_ready(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-I", "-B", str(WRAPPER_PATH), "--self-test"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={},
            cwd=WRAPPER_PATH.parent,
            check=False,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(completed.stderr, b"")
        document = verifier._strict_json_bytes(
            completed.stdout, "canonical wrapper self-test"
        )
        self.assertEqual(document["status"], "ready")

    def test_exact_fixed_routing_quota_is_accepted(self) -> None:
        roots, cells = _fixed_quota_inventories()
        verifier._verify_fixed_routing_quota_inventories(roots, cells)

    def test_smaller_batch_divisible_authority_is_rejected(self) -> None:
        roots, cells = _fixed_quota_inventories()
        roots["train"]["roots"] = 64
        roots["train"]["children"] = 256
        for cell in verifier.CELLS:
            cells["train"][cell]["roots"] = 8
        with self.assertRaisesRegex(ValueError, "routing quota"):
            verifier._verify_fixed_routing_quota_inventories(roots, cells)

    def test_4096_train_roots_with_cell_imbalance_is_rejected(self) -> None:
        roots, cells = _fixed_quota_inventories()
        cells["train"][verifier.CELLS[0]]["roots"] = 511
        cells["train"][verifier.CELLS[1]]["roots"] = 513
        self.assertEqual(roots["train"]["roots"], 4096)
        with self.assertRaisesRegex(ValueError, "routing quota"):
            verifier._verify_fixed_routing_quota_inventories(roots, cells)

    def test_routing_is_canonicalized_and_components_cover_whole_roots(self) -> None:
        database, roots, children, digest = self._database()
        try:
            self.assertEqual((roots, children), (1, 4))
            canonical = json.loads(
                database.connection.execute(
                    "SELECT canonical_json FROM routes"
                ).fetchone()[0]
            )
            self.assertEqual(
                [row["childId"] for row in canonical["children"]],
                ["child-1", "child-2", "child-3", "child-4"],
            )
            self.assertEqual(digest, verifier._stream_list_digest([canonical]))
        finally:
            database.close()

    def test_component_cross_split_is_rejected(self) -> None:
        routes, components = _route_rows()
        second = json.loads(json.dumps(routes[0]))
        second["rootId"] = "root-2"
        second["sourceRootId"] = "source-root-2"
        second["sourceGroupId"] = "source-group-2"
        for index, child in enumerate(second["children"]):
            child["childId"] = f"other-{index}"
        routes.append(second)
        other_component = dict(components[0])
        other_component.update(
            {
                "rootId": "root-2",
                "sourceRootId": "source-root-2",
                "sourceGroupId": "source-group-2",
                "split": "validation",
            }
        )
        components.append(other_component)
        routing = self.root / "routing.jsonl"
        component = self.root / "components.jsonl"
        _write_jsonl(routing, routes)
        _write_jsonl(component, components)
        with verifier.AuthorityDatabase() as database:
            roots_count, _, _ = verifier._parse_routes(
                routing, verifier._identity(routing), database
            )
            with self.assertRaisesRegex(ValueError, "whole-component"):
                verifier._parse_components(
                    component, verifier._identity(component), database, roots_count
                )

    def _initializer(
        self,
        *,
        tamper_health: bool = False,
        forge_source_model: bool = False,
        fallback: bool = False,
    ) -> tuple[Path, dict, dict]:
        model = self.root / "initializer.nnue"
        model.write_bytes(self.fallback_payload)
        foreign_model = self.root / "foreign-initializer.nnue"
        foreign_model.write_bytes(b"foreign initializer\n")
        producer = self.root / "initializer-producer.py"
        producer.write_text("# initializer\n", encoding="utf-8")
        g5_source_verifier = self.root / "g5-source-authority.py"
        g5_source_verifier.write_text("# reviewed G5 source\n", encoding="utf-8")
        g2_source_verifier = self.root / "g2-source-authority.py"
        g2_source_verifier.write_text("# reviewed G2 source\n", encoding="utf-8")
        g5_selection = self.root / "g5-selection.json"
        g5_closure = self.root / "g5-closure.json"
        g2_selection = self.root / "g2-selection.json"
        g2_closure = self.root / "g2-closure.json"
        _write_json(
            g5_selection,
            {
                "kind": "generation5-selection-seal",
                "status": "failed" if fallback else "selected-validation-winner",
                "selectedModel": None if fallback else verifier._identity(model),
                "healthPassed": None if fallback else not tamper_health,
            },
        )
        _write_json(
            g5_closure,
            {
                "kind": "generation5-terminal-closure",
                "outcome": "failed" if fallback else "promoted",
                "selection": verifier._identity(g5_selection),
                "winnerModel": None if fallback else verifier._identity(model),
                "winnerHealthPassed": None if fallback else True,
            },
        )
        _write_json(
            g2_selection,
            {
                "kind": "generation2-k2-selection-seal",
                "status": "forensically-unavailable",
                "selectedModel": None,
                "healthPassed": None,
            },
        )
        _write_json(
            g2_closure,
            {
                "kind": "generation2-k2-terminal-closure",
                "outcome": "unavailable",
                "selection": verifier._identity(g2_selection),
                "winnerModel": None,
                "winnerHealthPassed": None,
            },
        )
        catalog = [
            {
                "sourceId": "G5",
                "selectionSeal": verifier._identity(g5_selection),
                "closure": verifier._identity(g5_closure),
                "model": None if fallback else verifier._identity(model),
                "promotionStatus": "failed" if fallback else "promoted",
            },
            {
                "sourceId": "G2-K2",
                "selectionSeal": verifier._identity(g2_selection),
                "closure": verifier._identity(g2_closure),
                "model": None,
                "promotionStatus": "unavailable",
            },
        ]
        self._install_source_report(
            "G5",
            {
                "sourceId": "G5",
                "promotionStatus": "failed" if fallback else "promoted",
                "selectionSeal": verifier._identity(g5_selection),
                "closure": verifier._identity(g5_closure),
                "rawModel": verifier._identity(
                    foreign_model if forge_source_model else model
                ),
                "healthPassed": False if fallback else not tamper_health,
                "sourceVerifier": verifier._identity(g5_source_verifier),
                "unavailabilityEvidence": None,
                "resultInformationRead": False,
            },
            "g5",
        )
        self._install_source_report(
            "G2-K2",
            {
                "sourceId": "G2-K2",
                "promotionStatus": "unavailable",
                "selectionSeal": verifier._identity(g2_selection),
                "closure": verifier._identity(g2_closure),
                "rawModel": None,
                "healthPassed": False,
                "sourceVerifier": verifier._identity(g2_source_verifier),
                "unavailabilityEvidence": _synthetic_g2_unavailability_evidence(
                    self.root, "initializer"
                ),
                "resultInformationRead": False,
            },
            "g2",
        )
        selection = self.root / "initializer-selection.json"
        _write_json(
            selection,
            {
                "schemaVersion": 1,
                "kind": verifier.INITIALIZER_SELECTION_KIND,
                "profileId": verifier.PROFILE_ID,
                "selectionMode": (
                    "deterministic-fallback" if fallback else "promoted-prior"
                ),
                "selectedCatalogIndex": None if fallback else 0,
                "selectedModel": verifier._identity(model),
                "orderedCatalog": catalog,
                "fallbackProtocol": dict(verifier.FALLBACK_PROTOCOL),
                "g6TargetRowsDecoded": 0,
                "resultInformationRead": False,
            },
        )
        manifest = self.root / "initializer.manifest.json"
        _write_json(
            manifest,
            {
                "schemaVersion": 1,
                "kind": verifier.INITIALIZER_KIND,
                "profileId": verifier.PROFILE_ID,
                "status": "frozen-pre-g6-initializer-selection",
                "createdUtc": _timestamp(1),
                "resultInformationRead": False,
                "model": verifier._identity(model),
                "producer": verifier._identity(producer),
                "selectionMode": (
                    "deterministic-fallback" if fallback else "promoted-prior"
                ),
                "selectedCatalogIndex": None if fallback else 0,
                "selectionSeal": verifier._identity(selection),
                "sourceClosure": verifier._identity(g5_closure),
                "orderedCatalog": catalog,
                "fallbackProtocol": dict(verifier.FALLBACK_PROTOCOL),
            },
        )
        identities = {
            "initializerManifest": verifier._identity(manifest),
            "initializerModel": verifier._identity(model),
            "initializerSelection": verifier._identity(selection),
            "initializerClosure": verifier._identity(g5_closure),
        }
        return manifest, identities, {}

    def test_initializer_replays_embedded_health_and_closure(self) -> None:
        manifest, identities, capsule = self._initializer()
        _, result = verifier._verify_initializer(manifest, capsule, identities)
        self.assertIs(result["selectedPromotionHealthPassed"], True)
        self.assertEqual(result["catalogSourceIds"], ["G5", "G2-K2"])

    def test_initializer_rejects_false_promoted_health(self) -> None:
        manifest, identities, capsule = self._initializer(tamper_health=True)
        with self.assertRaisesRegex(ValueError, "health"):
            verifier._verify_initializer(manifest, capsule, identities)

    def test_initializer_rejects_forged_promoted_source_model(self) -> None:
        manifest, identities, capsule = self._initializer(
            forge_source_model=True
        )
        with self.assertRaisesRegex(ValueError, "model cross-link"):
            verifier._verify_initializer(manifest, capsule, identities)

    def test_authenticated_g2_unavailability_selects_exact_fallback(self) -> None:
        manifest, identities, capsule = self._initializer(fallback=True)
        _, result = verifier._verify_initializer(manifest, capsule, identities)
        self.assertEqual(result["selectionMode"], "deterministic-fallback")
        self.assertIsNone(result["selectedCatalogIndex"])
        self.assertTrue(result["fallbackProtocolReplayed"])

    def test_g2_unavailable_entry_cannot_name_a_model(self) -> None:
        manifest, _, _ = self._initializer(fallback=True)
        document = json.loads(manifest.read_text(encoding="utf-8"))
        entry = dict(document["orderedCatalog"][1])
        entry["model"] = document["model"]
        with self.assertRaisesRegex(ValueError, "null model"):
            verifier._verify_initializer_source_entry(entry, "G2-K2")

    def test_g5_unavailable_cannot_silently_fall_through(self) -> None:
        manifest, _, _ = self._initializer(fallback=True)
        document = json.loads(manifest.read_text(encoding="utf-8"))
        entry = dict(document["orderedCatalog"][0])
        entry["promotionStatus"] = "unavailable"
        with self.assertRaisesRegex(ValueError, "G5 has no terminal"):
            verifier._verify_initializer_source_entry(entry, "G5")

    def test_canonical_g2_honest_deep_mismatch_is_authenticated_unavailable(
        self,
    ) -> None:
        repository = MODULE_PATH.parents[3] / "senpai-omega-nnue"
        selection = (
            repository
            / "build-msvc/data-generation/deep-hce-v4/generation3-initializer-resolution.seal.json"
        )
        closure = (
            repository
            / "build-msvc/data-generation/deep-hce-v4/king-state-v1-prelabel.seal.json"
        )
        self.assertTrue(selection.is_file())
        self.assertTrue(closure.is_file())
        verifier._TEST_INITIALIZER_SOURCE_RUNNERS.pop("G2-K2", None)
        report = verifier._run_g2_source_replay(
            {
                "sourceId": "G2-K2",
                "selectionSeal": verifier._identity(selection),
                "closure": verifier._identity(closure),
                "model": None,
                "promotionStatus": "unavailable",
            }
        )
        self.assertEqual(report["promotionStatus"], "unavailable")
        self.assertIsNone(report["rawModel"])
        self.assertTrue(
            report["unavailabilityEvidence"]["fullReplayRejected"]
        )
        self.assertTrue(
            report["unavailabilityEvidence"]["deepReplayRejected"]
        )

    def test_fallback_generator_rejects_arbitrary_model_bytes(self) -> None:
        mutated = bytearray(self.fallback_payload)
        mutated[-1] ^= 1
        model = self.root / "forged-fallback.nnue"
        model.write_bytes(mutated)
        with self.assertRaisesRegex(ValueError, "exact generated bytes"):
            verifier._verify_fallback_model(verifier._identity(model))

    def test_g2_k2_mapping_is_exact_architecture4_append_zero(self) -> None:
        omega = verifier._load_pinned("omegaNnue").module
        raw = self.root / "raw-k2.nnue"
        mapped = self.root / "mapped-k2.nnue"
        network = omega.QuantizedNetwork(
            ft_bias=omega.np.zeros(omega.ACCUMULATOR_SIZE, dtype=omega.np.int16),
            ft_weights=omega.np.zeros(
                (omega.KING_STATE_FEATURE_COUNT, omega.ACCUMULATOR_SIZE),
                dtype=omega.np.int16,
            ),
            dense_bias=omega.np.zeros(omega.HIDDEN_SIZE, dtype=omega.np.int32),
            dense_weights=omega.np.zeros(
                (omega.HIDDEN_SIZE, omega.ACCUMULATOR_SIZE * 2),
                dtype=omega.np.int8,
            ),
            output_bias=0,
            output_weights=omega.np.zeros(omega.HIDDEN_SIZE, dtype=omega.np.int8),
            architecture=omega.ARCHITECTURE_KING_STATE_RESIDUAL,
        )
        raw.write_bytes(network.to_bytes())
        generator = MODULE_PATH.with_name("omega_decision_v3_initializer.py")
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                str(generator),
                "--write-g2-mapping",
                str(raw),
                str(mapped),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={},
            cwd=generator.parent,
            check=False,
            timeout=180,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, b"")
        verifier._verify_g2_mapping(
            verifier._identity(raw), verifier._identity(mapped)
        )
        forged = self.root / "forged-mapped-k2.nnue"
        payload = bytearray(mapped.read_bytes())
        payload[-1] ^= 1
        forged.write_bytes(payload)
        with self.assertRaisesRegex(ValueError, "exact architecture-4 bytes"):
            verifier._verify_g2_mapping(
                verifier._identity(raw), verifier._identity(forged)
            )

    def test_static_hce_replay_streams_and_matches_every_score(self) -> None:
        database, _, children, _ = self._database()
        try:
            runner = self.root / "fake-hce-runner.py"
            runner.write_text(
                "import sys\n"
                "assert sys.argv[1:] == ['--evaluate-handcrafted-stream']\n"
                "for line in sys.stdin:\n"
                "    print(int(line.split()[-1]) * 7)\n",
                encoding="utf-8",
            )
            transcript = self.root / "hce.jsonl"
            _write_jsonl(
                transcript,
                [
                    {
                        "schemaVersion": 1,
                        "kind": verifier.HCE_ROW_KIND,
                        "profileId": verifier.PROFILE_ID,
                        "childId": f"child-{index}",
                        "handcraftedCpChildStm": index * 7,
                    }
                    for index in range(1, 5)
                ],
            )
            verifier._run_hce_replay(
                engine_identity=verifier._identity(Path(sys.executable)),
                runner_identity=verifier._identity(runner),
                transcript_identity=verifier._identity(transcript),
                database=database,
                routed_children=children,
            )
        finally:
            database.close()

    def _attempt_row(
        self,
        teacher: object,
        context: object,
        child: object,
        stage: str,
        sequence: int,
        score: int,
    ) -> dict:
        nodes = verifier.STAGE_NODES[stage]
        stdout = [
            f"info depth 4 score cp {score} nodes {nodes} pv a0a1",
            f"info depth 5 nodes {nodes}",
            "bestmove a0a1",
        ]
        stdout_payload = "".join(line + "\n" for line in stdout).encode("utf-8")
        return {
            "schemaVersion": 1,
            "kind": verifier.TEACHER_ATTEMPT_KIND,
            "profileId": verifier.PROFILE_ID,
            "sequence": sequence,
            "claimSha256": context.identity["sha256"],
            "rootId": child.root_id,
            "childId": child.child_id,
            "normalizedChildOfen": child.ofen,
            "stage": stage,
            "nodes": nodes,
            "attempt": 1,
            "startedUtc": _timestamp(3 + sequence * 2),
            "completedUtc": _timestamp(4 + sequence * 2),
            "elapsedMilliseconds": 1,
            "processCommand": [context.document["engine"]["path"]],
            "workingDirectory": str(Path(context.document["engine"]["path"]).parent),
            "environment": {},
            "uciCommandsSha256": teacher._digest(teacher._uci_commands(child, nodes)),
            "exitCode": 0,
            "timedOut": False,
            "transcriptComplete": True,
            "stdoutLines": stdout,
            "stderrLines": [],
            "stdoutSha256": hashlib.sha256(stdout_payload).hexdigest(),
            "stderrSha256": hashlib.sha256(b"").hexdigest(),
            "outcome": "success",
            "scoreCpChildStm": score,
            "reportedNodes": nodes,
            "exactCp": True,
            "resultInformationRead": False,
        }

    def test_teacher_ledger_replays_both_stages_and_raw_transcripts(self) -> None:
        teacher = verifier._load_pinned("teacher").module
        database, _, children, route_digest = self._database()
        try:
            engine = Path(sys.executable)
            claim_path = self.root / "teacher-claim.json"
            claim_path.write_text("claim\n", encoding="utf-8")
            claim_identity = verifier._identity(claim_path)
            claim = {
                "createdUtc": _timestamp(2),
                "engine": verifier._identity(engine),
            }
            context = teacher.ClaimContext(claim_path, claim_identity, claim, (), {})
            child_values = [
                teacher.Child(root_id, child_id, ofen, "endgame", "w")
                for root_id, child_id, ofen in database.connection.execute(
                    "SELECT root_id,child_id,ofen FROM children ORDER BY root_id,child_id"
                )
            ]
            rows: list[dict] = []
            sequence = 0
            for stage in verifier.STAGES:
                for index, child in enumerate(child_values):
                    rows.append(
                        self._attempt_row(
                            teacher, context, child, stage, sequence, (index + 1) * 10
                        )
                    )
                    sequence += 1
            ledger = self.root / "attempts.jsonl"
            _write_jsonl(ledger, rows)
            deep_digest = verifier._stream_list_digest(
                {"childId": child.child_id, "scoreCpChildStm": (index + 1) * 10}
                for index, child in enumerate(child_values)
            )
            receipt = self.root / "attempts.complete.json"
            receipt_document = {
                "schemaVersion": 1,
                "kind": verifier.LEDGER_COMPLETION_KIND,
                "profileId": verifier.PROFILE_ID,
                "status": "complete-exact-child-coverage",
                "createdUtc": _timestamp(100),
                "claim": claim_identity,
                "attemptLedger": verifier._identity(ledger),
                "budgets": dict(verifier.TEACHER_BUDGETS),
                "inputOrderSha256": route_digest,
                "routedChildren": children,
                "attemptRecords": len(rows),
                "successfulChildren": children,
                "rejectedChildren": 0,
                "unresolvedChildren": 0,
                "deepScoresSha256": deep_digest,
                "resultInformationRead": False,
                "finalStageSeal": True,
            }
            _write_json(receipt, receipt_document)
            identities = {
                "teacherAttemptLedger": verifier._identity(ledger),
                "teacherAttemptLedgerCompletion": verifier._identity(receipt),
                "teacherClaim": claim_identity,
            }
            _, total, deep, _ = verifier._replay_teacher_ledger(
                identities=identities,
                claim=claim,
                claim_context=context,
                database=database,
                routed_children=children,
                route_digest=route_digest,
                teacher_module=teacher,
            )
            self.assertEqual((total, deep), (8, 4))
        finally:
            database.close()

    def test_identity_rejects_hardlinks(self) -> None:
        source = self.root / "source.bin"
        alias = self.root / "alias.bin"
        source.write_bytes(b"same inode\n")
        try:
            os.link(source, alias)
        except OSError:
            self.skipTest("hard links are unavailable")
        with self.assertRaisesRegex(ValueError, "unlinked"):
            verifier._identity(source)

    def test_production_cli_emits_no_stdout_on_unpinned_failure(self) -> None:
        # Exercise a pristine subprocess: moving terminal/routing pins are not
        # silently accepted by the production executable.
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                str(MODULE_PATH),
                "--verify-omega-decision-v3-capsule",
                str(self.root / "absent-capsule"),
                str(self.root / "absent-options"),
                str(self.root / "absent-initializer"),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={},
            cwd=MODULE_PATH.parent,
            check=False,
            timeout=30,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, b"")


class RealProducerIntegrationTests(unittest.TestCase):
    """Feed a real teacher run into the independent streaming replay."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.prior_catalog_dependency = verifier._load_pinned("priorCatalog")
        cls.fallback_temporary = tempfile.TemporaryDirectory(
            prefix="omega-decision-v3-real-fallback-fixture-"
        )
        cls.fallback_payload = _generate_fallback_payload(
            Path(cls.fallback_temporary.name)
        )
        source = MODULE_PATH.with_name("test_omega_decision_v3_teacher.py")
        spec = importlib.util.spec_from_file_location(
            "omega_decision_v3_teacher_integration_fixture", source
        )
        assert spec is not None and spec.loader is not None
        cls.fixture_module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.fixture_module
        spec.loader.exec_module(cls.fixture_module)
        cls.fixture_module.TeacherRunnerTests.setUpClass()
        terminal_source = MODULE_PATH.with_name(
            "test_omega_decision_v3_terminal_lineage.py"
        )
        terminal_spec = importlib.util.spec_from_file_location(
            "omega_decision_v3_terminal_integration_fixture", terminal_source
        )
        assert terminal_spec is not None and terminal_spec.loader is not None
        cls.terminal_fixture_module = importlib.util.module_from_spec(terminal_spec)
        sys.modules[terminal_spec.name] = cls.terminal_fixture_module
        terminal_spec.loader.exec_module(cls.terminal_fixture_module)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture_module.TeacherRunnerTests.tearDownClass()
        cls.fallback_temporary.cleanup()

    def setUp(self) -> None:
        verifier._TEST_INITIALIZER_SOURCE_RUNNERS.clear()

    def tearDown(self) -> None:
        verifier._TEST_INITIALIZER_SOURCE_RUNNERS.clear()

    def test_real_teacher_producer_is_not_accepted_by_echo(self) -> None:
        teacher = verifier._load_pinned("teacher").module
        with tempfile.TemporaryDirectory(
            prefix="omega-verifier-real-teacher-"
        ) as directory:
            fixture = self.fixture_module.Fixture(
                Path(directory),
                self.fixture_module.TeacherRunnerTests.engine_bundle,
            )
            state = fixture.run()
            self.assertTrue(state.complete)
            teacher.publish_teacher_outputs(
                claim=fixture.claim,
                ledger=fixture.ledger,
                ledger_completion=fixture.ledger_completion,
                labels=fixture.labels,
                teacher_manifest=fixture.teacher_manifest,
            )
            with verifier.AuthorityDatabase() as database:
                roots, children, route_digest = verifier._parse_routes(
                    fixture.routing, verifier._identity(fixture.routing), database
                )
                verifier._parse_components(
                    fixture.components,
                    verifier._identity(fixture.components),
                    database,
                    roots,
                )
                context = teacher._verify_claim(fixture.claim)
                claim = dict(context.document)
                identities = {
                    "teacherAttemptLedger": verifier._identity(fixture.ledger),
                    "teacherAttemptLedgerCompletion": verifier._identity(
                        fixture.ledger_completion
                    ),
                    "teacherClaim": verifier._identity(fixture.claim),
                }
                receipt, total, deep, _ = verifier._replay_teacher_ledger(
                    identities=identities,
                    claim=claim,
                    claim_context=context,
                    database=database,
                    routed_children=children,
                    route_digest=route_digest,
                    teacher_module=teacher,
                )
                self.assertEqual(receipt["attemptRecords"], total)
                self.assertEqual(deep, children)
                self.assertTrue(
                    all(
                        row[0] is not None
                        for row in database.connection.execute(
                            "SELECT deep_score FROM children"
                        )
                    )
                )

    def test_miniature_real_capsule_cannot_bypass_exact_quota(self) -> None:
        trainer = verifier._load_pinned("trainer").module
        teacher = verifier._load_pinned("teacher").module
        routing_module = verifier._load_pinned("routing").module
        terminal = self.terminal_fixture_module.terminal
        with tempfile.TemporaryDirectory(
            prefix="omega-verifier-real-capsule-"
        ) as directory:
            root = Path(directory)

            terminal_fixture = self.terminal_fixture_module.TerminalFixture(
                root / "terminal"
            )
            terminal_documents = terminal_fixture.run_claimed_classifier()
            terminal.publish_terminal_lineage(
                terminal_fixture.lineage, **terminal_fixture.lineage_kwargs()
            )
            source_root = terminal_documents["roots"][0]
            source_children = sorted(
                (
                    row
                    for row in terminal_documents["children"]
                    if row["rootId"] == source_root["rootId"]
                ),
                key=lambda row: row["childId"],
            )[:4]
            self.assertEqual(len(source_children), 4)
            phase, side, _ = routing_module._phase_side(
                source_root["rootOfen"], "miniature routed root"
            )
            routing_path = root / "routing.jsonl"
            component_path = root / "components.jsonl"
            route_rows = [
                {
                    "schemaVersion": 1,
                    "kind": verifier.ROUTING_KIND,
                    "profileId": verifier.PROFILE_ID,
                    "rootId": source_root["rootId"],
                    "sourceRootId": source_root["rootId"],
                    "sourceGroupId": source_root["groupId"],
                    "phase": phase,
                    "parentSideToMove": side,
                    "children": [
                        {
                            "childId": row["childId"],
                            "normalizedChildOfen": row["childOfen"],
                        }
                        for row in source_children
                    ],
                }
            ]
            component_rows = [
                {
                    "schemaVersion": 1,
                    "kind": verifier.COMPONENT_KIND,
                    "profileId": verifier.PROFILE_ID,
                    "rootId": source_root["rootId"],
                    "leakageComponentId": "component-miniature",
                    "split": "train",
                    "sourceRootId": source_root["rootId"],
                    "sourceGroupId": source_root["groupId"],
                }
            ]
            _write_jsonl(routing_path, route_rows)
            _write_jsonl(component_path, component_rows)

            prior_source = root / "prior-source.jsonl"
            _write_jsonl(prior_source, [{"source": "G3/G4/G5 miniature prior"}])
            prior_producer = root / "prior-producer.py"
            prior_producer.write_text("# prior catalog producer\n", encoding="utf-8")
            prior_ofen = (
                "5k4/10/10/10/10/4P5/10/10/10/4K5[-/-/-/-] w - - 0 1"
            )
            exact, orbit, signatures = routing_module._leakage_keys(prior_ofen)
            prior_row = {
                "schemaVersion": 1,
                "kind": routing_module.FORBIDDEN_ROW_KIND,
                "ofen": prior_ofen,
                "exactPositionKey": exact,
                "conservativeOrbitKey": orbit,
                "conservativeOrbitSignatures": list(signatures),
                "sourceGameId": "miniature-prior-game",
                "sourceRunId": "miniature-prior-run",
                "sourceArtifactSha256": verifier._identity(prior_source)["sha256"],
            }
            prior_row["positionId"] = routing_module._catalog_position_id(prior_row)
            prior_catalog = root / "prior-catalog.jsonl"
            _write_jsonl(prior_catalog, [prior_row])
            prior_manifest = root / "prior-catalog.manifest.json"
            source_inventory = [verifier._identity(prior_source)]
            empty_digest = routing_module._digest([])
            _write_json(
                prior_manifest,
                {
                    "schemaVersion": 1,
                    "kind": routing_module.FORBIDDEN_MANIFEST_KIND,
                    "targetOpaque": True,
                    "createdUtc": "2026-07-23T00:00:00Z",
                    "catalog": verifier._identity(prior_catalog),
                    "catalogSchema": {
                        "recognizedFields": sorted(routing_module.FORBIDDEN_ROW_FIELDS),
                        "unknownFieldPolicy": "abort",
                        "minimumPositionIdentity": (
                            "at least one exact or conservative orbit signature per row"
                        ),
                    },
                    "sourceInventory": source_inventory,
                    "sourceInventorySha256": routing_module._digest(source_inventory),
                    "sourceProjectionManifests": [],
                    "sourceProjectionManifestsSha256": empty_digest,
                    "priorSourceAudits": [],
                    "priorSourceAuditsSha256": empty_digest,
                    "extractionPolicy": {"policyId": "miniature-real-capsule-v1"},
                    "positionCount": 1,
                    "targetOrScoreFieldsDecoded": 0,
                    "targetOrScoreFieldsEmitted": 0,
                    "producer": verifier._identity(prior_producer),
                },
            )
            registry_producer = root / "registry-producer.py"
            registry_producer.write_text("# registry producer\n", encoding="utf-8")
            forbidden_registry = root / "prior-forbidden.registry.json"
            trainer.publish_upstream_forbidden_registry(
                forbidden_registry,
                catalog_groups=((verifier.PRIOR_SOURCE_IDS, prior_manifest),),
                producer=registry_producer,
                created_utc=_timestamp(6),
            )

            initializer = root / "initializer.nnue"
            initializer.write_bytes(self.fallback_payload)
            initializer_producer = root / "initializer-producer.py"
            initializer_producer.write_text("# initializer producer\n", encoding="utf-8")
            g5_source_verifier = root / "g5-source-authority.py"
            g5_source_verifier.write_text("# reviewed G5 source\n", encoding="utf-8")
            g2_source_verifier = root / "g2-source-authority.py"
            g2_source_verifier.write_text("# reviewed G2 source\n", encoding="utf-8")
            g5_selection = root / "g5-selection.json"
            _write_json(
                g5_selection,
                {
                    "kind": "generation5-selection-seal",
                    "status": "selected-validation-winner",
                    "selectedModel": verifier._identity(initializer),
                    "healthPassed": True,
                },
            )
            g5_closure = root / "g5-closure.json"
            _write_json(
                g5_closure,
                {
                    "kind": "generation5-terminal-closure",
                    "outcome": "promoted",
                    "selection": verifier._identity(g5_selection),
                    "winnerModel": verifier._identity(initializer),
                    "winnerHealthPassed": True,
                },
            )
            g2_selection = root / "g2-selection.json"
            _write_json(
                g2_selection,
                {
                    "kind": "generation2-k2-selection-seal",
                    "status": "forensically-unavailable",
                    "selectedModel": None,
                    "healthPassed": None,
                },
            )
            g2_closure = root / "g2-closure.json"
            _write_json(
                g2_closure,
                {
                    "kind": "generation2-k2-terminal-closure",
                    "outcome": "unavailable",
                    "selection": verifier._identity(g2_selection),
                    "winnerModel": None,
                    "winnerHealthPassed": None,
                },
            )
            initializer_catalog = [
                {
                    "sourceId": "G5",
                    "selectionSeal": verifier._identity(g5_selection),
                    "closure": verifier._identity(g5_closure),
                    "model": verifier._identity(initializer),
                    "promotionStatus": "promoted",
                },
                {
                    "sourceId": "G2-K2",
                    "selectionSeal": verifier._identity(g2_selection),
                    "closure": verifier._identity(g2_closure),
                    "model": None,
                    "promotionStatus": "unavailable",
                },
            ]
            _install_source_report(
                root,
                "G5",
                {
                    "sourceId": "G5",
                    "promotionStatus": "promoted",
                    "selectionSeal": verifier._identity(g5_selection),
                    "closure": verifier._identity(g5_closure),
                    "rawModel": verifier._identity(initializer),
                    "healthPassed": True,
                    "sourceVerifier": verifier._identity(g5_source_verifier),
                    "unavailabilityEvidence": None,
                    "resultInformationRead": False,
                },
                "g5",
            )
            _install_source_report(
                root,
                "G2-K2",
                {
                    "sourceId": "G2-K2",
                    "promotionStatus": "unavailable",
                    "selectionSeal": verifier._identity(g2_selection),
                    "closure": verifier._identity(g2_closure),
                    "rawModel": None,
                    "healthPassed": False,
                    "sourceVerifier": verifier._identity(g2_source_verifier),
                    "unavailabilityEvidence": _synthetic_g2_unavailability_evidence(
                        root, "real-producer"
                    ),
                    "resultInformationRead": False,
                },
                "g2",
            )
            initializer_selection = root / "initializer-selection.json"
            _write_json(
                initializer_selection,
                {
                    "schemaVersion": 1,
                    "kind": verifier.INITIALIZER_SELECTION_KIND,
                    "profileId": verifier.PROFILE_ID,
                    "selectionMode": "promoted-prior",
                    "selectedCatalogIndex": 0,
                    "selectedModel": verifier._identity(initializer),
                    "orderedCatalog": initializer_catalog,
                    "fallbackProtocol": dict(verifier.FALLBACK_PROTOCOL),
                    "g6TargetRowsDecoded": 0,
                    "resultInformationRead": False,
                },
            )
            initializer_manifest = root / "initializer.manifest.json"
            _write_json(
                initializer_manifest,
                {
                    "schemaVersion": 1,
                    "kind": verifier.INITIALIZER_KIND,
                    "profileId": verifier.PROFILE_ID,
                    "status": "frozen-pre-g6-initializer-selection",
                    "createdUtc": _timestamp(6),
                    "resultInformationRead": False,
                    "model": verifier._identity(initializer),
                    "producer": verifier._identity(initializer_producer),
                    "selectionMode": "promoted-prior",
                    "selectedCatalogIndex": 0,
                    "selectionSeal": verifier._identity(initializer_selection),
                    "sourceClosure": verifier._identity(g5_closure),
                    "orderedCatalog": initializer_catalog,
                    "fallbackProtocol": dict(verifier.FALLBACK_PROTOCOL),
                },
            )

            source_roots_manifest = root / "source-roots.manifest.json"
            source_children_manifest = root / "source-children.manifest.json"
            _write_json(source_roots_manifest, {"roots": 1, "targetFree": True})
            _write_json(source_children_manifest, {"children": 4, "targetFree": True})
            prelabel_producer = root / "prelabel-producer.py"
            prelabel_producer.write_text("# prelabel producer\n", encoding="utf-8")
            prelabel = root / "prelabel.json"
            trainer.publish_upstream_prelabel_seal(
                prelabel,
                component_map=component_path,
                target_free_routing=routing_path,
                terminal_classifier_lineage=terminal_fixture.lineage,
                prior_forbidden_registry=forbidden_registry,
                prior_forbidden_catalogs=(prior_manifest,),
                initializer_manifest=initializer_manifest,
                source_root_manifest=source_roots_manifest,
                source_children_manifest=source_children_manifest,
                producer=prelabel_producer,
                created_utc=_timestamp(7),
            )

            hce_options = root / "hce-options.json"
            _write_json(hce_options, trainer.static_hce_options_document())
            hce_runner = MODULE_PATH.with_name("omega_decision_v3_evaluator_runner.py")
            hce_transcript = root / "hce.jsonl"
            hce_claim = root / "hce.claim.json"
            _write_json(
                hce_claim,
                {
                    "schemaVersion": 1,
                    "kind": verifier.HCE_CLAIM_KIND,
                    "profileId": verifier.PROFILE_ID,
                    "status": "claimed-before-teacher-and-target-decode",
                    "createdUtc": _timestamp(8),
                    "prelabelSeal": verifier._identity(prelabel),
                    "targetFreeRouting": verifier._identity(routing_path),
                    "engine": verifier._identity(Path(sys.executable)),
                    "runner": verifier._identity(hce_runner),
                    "options": verifier._identity(hce_options),
                    "plannedTranscriptPath": str(hce_transcript.resolve()),
                    "targetRowsDecodedAtClaim": 0,
                    "targetFieldsDecodedAtClaim": 0,
                    "resultInformationRead": False,
                },
            )
            ordered_children = sorted(
                route_rows[0]["children"], key=lambda row: row["childId"]
            )
            hce_scores = trainer._run_exact_evaluator(
                engine=Path(sys.executable),
                runner=hce_runner,
                mode="--evaluate-handcrafted-stream",
                ofens=[row["normalizedChildOfen"] for row in ordered_children],
            )
            _write_jsonl(
                hce_transcript,
                [
                    {
                        "schemaVersion": 1,
                        "kind": verifier.HCE_ROW_KIND,
                        "profileId": verifier.PROFILE_ID,
                        "childId": row["childId"],
                        "handcraftedCpChildStm": hce_scores[str(index)],
                    }
                    for index, row in enumerate(ordered_children)
                ],
            )
            hce_completion = root / "hce.completion.json"
            trainer.publish_upstream_hce_completion(
                hce_completion,
                claim=hce_claim,
                prelabel_seal=prelabel,
                target_free_routing=routing_path,
                engine=Path(sys.executable),
                runner=hce_runner,
                options=hce_options,
                transcript=hce_transcript,
                created_utc=_timestamp(9),
            )

            engine_directory = root / "teacher-engine"
            shutil.copytree(
                self.fixture_module.TeacherRunnerTests.engine_bundle,
                engine_directory,
            )
            teacher_engine = engine_directory / "SenpaiFakeG6.exe"
            (engine_directory / "behavior.txt").write_text("", encoding="utf-8")
            teacher_options = root / "teacher-options.json"
            teacher.publish_teacher_options(teacher_options)
            projection_producer = root / "projection-producer.py"
            projection_producer.write_text("# projection producer\n", encoding="utf-8")
            projected_corpus = root / "projected.jsonl"
            label_manifest = root / "labels.manifest.json"
            teacher_claim = root / "teacher.claim.json"
            teacher.publish_teacher_claim(
                teacher_claim,
                routing=routing_path,
                components=component_path,
                prelabel=prelabel,
                hce_completion=hce_completion,
                engine=teacher_engine,
                options=teacher_options,
                projection_producer=projection_producer,
                projected_corpus=projected_corpus,
                projection_manifest=label_manifest,
                created_utc=_timestamp(10),
            )
            attempt_ledger = root / "teacher-attempts.jsonl"
            teacher.run_teacher(teacher_claim, attempt_ledger, resume=False)
            ledger_completion = root / "teacher-attempts.complete.json"
            teacher_labels = root / "teacher-labels.jsonl"
            teacher_manifest = root / "teacher.manifest.json"
            teacher.publish_teacher_outputs(
                claim=teacher_claim,
                ledger=attempt_ledger,
                ledger_completion=ledger_completion,
                labels=teacher_labels,
                teacher_manifest=teacher_manifest,
            )
            teacher_context = teacher._verify_claim(teacher_claim)
            teacher_rows, _ = teacher._parse_teacher_labels(
                teacher_labels, teacher_context
            )
            teacher._exclusive_jsonl(
                projected_corpus,
                teacher._projected_rows(teacher_rows, teacher_context.components),
            )
            teacher_manifest_document = json.loads(
                teacher_manifest.read_text(encoding="utf-8")
            )
            teacher._exclusive_json(
                label_manifest,
                teacher.projected_manifest_document(
                    context=teacher_context,
                    corpus=projected_corpus,
                    teacher_labels=teacher_labels,
                    teacher_manifest=teacher_manifest,
                    created_utc=teacher._utc_after(
                        teacher_manifest_document["createdUtc"]
                    ),
                ),
            )
            teacher_completion = root / "teacher.completion.json"
            teacher.finalize_teacher(
                claim=teacher_claim,
                ledger=attempt_ledger,
                ledger_completion=ledger_completion,
                labels=teacher_labels,
                teacher_manifest=teacher_manifest,
                completion=teacher_completion,
            )

            label_document = json.loads(label_manifest.read_text(encoding="utf-8"))
            completion_document = json.loads(
                teacher_completion.read_text(encoding="utf-8")
            )
            hce_manifest = root / "hce.manifest.json"
            trainer.publish_static_hce_manifest(
                hce_manifest,
                corpus=projected_corpus,
                label_manifest=label_manifest,
                component_map=component_path,
                projection=hce_transcript,
                engine=Path(sys.executable),
                runner=hce_runner,
                options=hce_options,
                created_utc=teacher._utc_after(
                    label_document["createdUtc"], completion_document["createdUtc"]
                ),
            )
            hce_manifest_document = json.loads(hce_manifest.read_text(encoding="utf-8"))
            verifier_options = root / "verifier-options.json"
            verifier.publish_verifier_options(verifier_options)
            capsule_path = root / "capsule.closure.json"
            capsule = {
                "schemaVersion": 1,
                "kind": verifier.CAPSULE_KIND,
                "profileId": verifier.PROFILE_ID,
                "status": "closed-pretarget-to-final-projection-lineage",
                "createdUtc": teacher._utc_after(
                    hce_manifest_document["createdUtc"],
                    completion_document["createdUtc"],
                ),
                "upstreamVerifierExecutable": verifier._identity(Path(sys.executable)),
                "upstreamVerifierRunner": verifier._identity(MODULE_PATH),
                "upstreamVerifierOptions": verifier._identity(verifier_options),
                "targetFreeRouting": verifier._identity(routing_path),
                "componentMap": verifier._identity(component_path),
                "prelabelSeal": verifier._identity(prelabel),
                "terminalClassifierLineage": verifier._identity(terminal_fixture.lineage),
                "initializerSelection": verifier._identity(initializer_selection),
                "initializerClosure": verifier._identity(g5_closure),
                "initializerModel": verifier._identity(initializer),
                "initializerManifest": verifier._identity(initializer_manifest),
                "plannedProjectionProducer": verifier._identity(projection_producer),
                "plannedProjectedCorpusPath": str(projected_corpus.resolve()),
                "plannedProjectionManifestPath": str(label_manifest.resolve()),
                "priorForbiddenRegistry": verifier._identity(forbidden_registry),
                "priorForbiddenCatalogs": [verifier._identity(prior_manifest)],
                "teacherClaim": verifier._identity(teacher_claim),
                "teacherEngine": verifier._identity(teacher_engine),
                "teacherRunner": verifier._identity(
                    MODULE_PATH.with_name("omega_decision_v3_teacher.py")
                ),
                "teacherOptions": verifier._identity(teacher_options),
                "teacherBudgets": dict(verifier.TEACHER_BUDGETS),
                "teacherInputOrderSha256": teacher_context.document[
                    "inputOrderSha256"
                ],
                "teacherAttemptLedger": verifier._identity(attempt_ledger),
                "teacherAttemptLedgerCompletion": verifier._identity(
                    ledger_completion
                ),
                "teacherCompletion": verifier._identity(teacher_completion),
                "projectionProducer": verifier._identity(projection_producer),
                "teacherLabels": verifier._identity(teacher_labels),
                "teacherManifest": verifier._identity(teacher_manifest),
                "projectedCorpus": verifier._identity(projected_corpus),
                "labelManifest": verifier._identity(label_manifest),
                "preTargetHceClaim": verifier._identity(hce_claim),
                "preTargetHceCompletion": verifier._identity(hce_completion),
                "staticHceEngine": verifier._identity(Path(sys.executable)),
                "staticHceRunner": verifier._identity(hce_runner),
                "staticHceOptions": verifier._identity(hce_options),
                "staticHceTranscript": verifier._identity(hce_transcript),
                "staticHceManifest": verifier._identity(hce_manifest),
                "rootInventories": label_document["rootInventories"],
                "phaseSideInventories": label_document["phaseSideInventories"],
                "closureDeclaration": dict(verifier.CAPSULE_DECLARATION),
                "resultInformationRead": False,
                "finalStageSeal": True,
            }
            _write_json(capsule_path, capsule)
            with self.assertRaisesRegex(ValueError, "exact phase/side quota"):
                verifier.verify_capsule(
                    capsule_path, verifier_options, initializer_manifest
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
