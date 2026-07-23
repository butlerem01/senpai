#!/usr/bin/env python3
"""Small standard-library regression tests for the decision-data plumbing."""

from __future__ import annotations

import tempfile
import hashlib
import contextlib
import io
import json
from pathlib import Path
import socket
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import omega_decision_teacher as decision


class DecisionTeacherTests(unittest.TestCase):
    def test_internal_self_test(self) -> None:
        decision._self_test()

    def test_jsonl_round_trip_and_no_clobber(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.jsonl"
            rows = [{"rootId": "a"}, {"rootId": "b"}]
            decision._atomic_jsonl(path, rows)
            self.assertEqual([row for _, row in decision._jsonl(path)], rows)
            with self.assertRaises(FileExistsError):
                decision._atomic_jsonl(path, rows)

    def test_no_clobber_publication_is_race_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "one-winner.json"
            barrier = threading.Barrier(2)
            outcomes: list[str] = []

            def publish(value: int) -> None:
                barrier.wait()
                try:
                    decision._atomic_json(path, {"value": value})
                    outcomes.append("published")
                except FileExistsError:
                    outcomes.append("refused")

            threads = [threading.Thread(target=publish, args=(value,)) for value in (1, 2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertCountEqual(outcomes, ["published", "refused"])

    def test_hard_negative_interval_is_fail_closed(self) -> None:
        children = []
        shallow = {}
        parent = "k9/10/10/10/10/10/10/10/10/9K[-/-/-/-] w - - 0 1"
        child_ofen = "k9/10/10/10/10/10/10/10/10/9K[-/-/-/-] b - - 1 1"
        for rank in range(1, 5):
            child_id = f"child-{rank}"
            children.append(
                {
                    "childId": child_id,
                    "rootId": "root",
                    "groupId": "group",
                    "sourceGameId": "run:game:a1",
                    "move": f"a0a{rank}",
                    "rootPvMove": "a0a4",
                    "parentOfen": parent,
                    "childOfen": child_ofen,
                }
            )
            shallow[child_id] = {"scoreCpRoot": 100 - rank}
        with self.assertRaisesRegex(ValueError, "ranks 4-12"):
            decision._choose_four(
                children, shallow, seed=decision.SIBLING_EXPLORATION_SEED
            )

    def test_target_opaque_forbidden_catalog_contract(self) -> None:
        ofen = "k9/10/10/10/10/10/10/10/10/9K[-/-/-/-] w - - 0 1"
        with tempfile.TemporaryDirectory() as directory:
            _, manifest = decision._build_synthetic_forbidden_catalog(
                Path(directory),
                [
                    {
                        "ofen": ofen,
                        "sourceGameId": "prior:game:a1",
                        "sourceRunId": "prior",
                        "sourceInputSha256": "a" * 64,
                    }
                ],
            )
            forbidden, pins = decision._load_forbidden_catalogs([manifest])
            exact, _, signatures = decision.deep._leakage_keys(ofen)
            self.assertIn(exact, forbidden["exact"])
            self.assertTrue(set(signatures) <= forbidden["signatures"])
            self.assertEqual(forbidden["sourceGameIds"], {"prior:game:a1"})
            self.assertEqual(len(pins), 1)

    def test_forbidden_catalog_rejects_undeclared_target_field(self) -> None:
        ofen = "k9/10/10/10/10/10/10/10/10/9K[-/-/-/-] w - - 0 1"
        with tempfile.TemporaryDirectory() as directory:
            _, manifest = decision._build_synthetic_forbidden_catalog(
                Path(directory), [{"ofen": ofen, "teacherScore": 12}]
            )
            with self.assertRaisesRegex(ValueError, "undeclared fields"):
                decision._load_forbidden_catalogs([manifest])

    def test_split_name_and_phase_coverage_are_fail_closed(self) -> None:
        child = {
            "childId": "child",
            "rootId": "root",
            "groupId": "group",
            "sourceGameId": "run:game:a1",
            "parentOfen": "k9/10/10/10/10/10/10/10/10/9K[-/-/-/-] w - - 0 1",
            "childOfen": "k9/10/10/10/10/10/10/10/10/9K[-/-/-/-] b - - 1 1",
        }
        _, splits = decision._component_splits(
            [child], seed=decision.COMPONENT_SPLIT_SEED,
            train_percent=80.0, validation_percent=10.0
        )
        self.assertTrue(set(splits.values()) <= set(decision.SPLITS))
        self.assertNotIn("test", splits.values())
        with self.assertRaisesRegex(ValueError, "phase coverage"):
            decision._assert_all_phases_per_split(
                [{"rootId": "r", "phase": "opening", "split": "train"}],
                description="synthetic",
            )

    def test_persistent_uci_session_reuses_and_restarts(self) -> None:
        class FakeUci:
            instances: list["FakeUci"] = []
            fail_next_search = False

            def __init__(self, _engine: Path):
                self.last = ""
                self.closed = False
                self.commands: list[str] = []
                self.__class__.instances.append(self)

            def send(self, command: str) -> None:
                self.last = command
                self.commands.append(command)

            def until(self, _predicate, _timeout):
                if self.last == "uci":
                    return ["uciok"], []
                if self.last == "isready":
                    return ["readyok"], []
                if self.last.startswith("go nodes"):
                    if self.__class__.fail_next_search:
                        self.__class__.fail_next_search = False
                        raise TimeoutError("synthetic timeout")
                    nodes = int(self.last.split()[-1])
                    return [
                        f"info depth 1 score cp 12 nodes {nodes // 2} pv a0a1",
                        f"info depth 2 nodes {nodes}",
                        "bestmove a0a1",
                    ], []
                raise AssertionError(self.last)

            def close(self) -> None:
                self.closed = True

        prior = decision._UCI_FACTORY
        decision._UCI_FACTORY = FakeUci
        reverifications = []
        session = decision._PersistentHceSession(
            Path("fake-engine"), lambda: reverifications.append(True)
        )
        try:
            session.search("ofen-a", 2000, 1.0)
            session.search("ofen-b", 2000, 1.0)
            self.assertEqual(len(FakeUci.instances), 1)
            self.assertEqual(
                FakeUci.instances[0].commands.count("setoption name Clear Hash"), 2
            )
            FakeUci.fail_next_search = True
            with self.assertRaises(TimeoutError):
                session.search("ofen-c", 2000, 1.0)
            session.search("ofen-d", 2000, 1.0)
            self.assertEqual(len(FakeUci.instances), 2)
            self.assertEqual(session.restarts, 1)
            self.assertEqual(len(reverifications), 2)
        finally:
            session.close()
            decision._UCI_FACTORY = prior

    def test_identical_ledger_claim_never_allows_two_writers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "shallow.jsonl"
            claim_path = Path(str(ledger) + ".active.claim.json")
            first = decision._ExclusiveLedgerClaim(
                claim_path,
                ledger=ledger,
                stage="shallow",
                lock_sha256="a" * 64,
                recover_stale=False,
            )
            try:
                with self.assertRaisesRegex(RuntimeError, "active/stale claim"):
                    decision._ExclusiveLedgerClaim(
                        claim_path,
                        ledger=ledger,
                        stage="shallow",
                        lock_sha256="a" * 64,
                        recover_stale=False,
                    )
                with self.assertRaisesRegex(RuntimeError, "owner PID is alive"):
                    decision._ExclusiveLedgerClaim(
                        claim_path,
                        ledger=ledger,
                        stage="shallow",
                        lock_sha256="a" * 64,
                        recover_stale=True,
                    )
            finally:
                first.release()

    def test_stale_claim_requires_explicit_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "deep.jsonl"
            claim_path = Path(str(ledger) + ".active.claim.json")
            decision._atomic_json(
                claim_path,
                {
                    "schemaVersion": 1,
                    "kind": "omega-decision-active-ledger-claim",
                    "stage": "deep",
                    "ledger": str(ledger.resolve()),
                    "lockSha256": "b" * 64,
                    "host": socket.gethostname(),
                    "pid": 2_147_483_647,
                    "token": "stale",
                    "createdUtc": "2026-07-21T00:00:00Z",
                },
            )
            with self.assertRaisesRegex(RuntimeError, "--recover-stale-claim"):
                decision._ExclusiveLedgerClaim(
                    claim_path,
                    ledger=ledger,
                    stage="deep",
                    lock_sha256="b" * 64,
                    recover_stale=False,
                )
            recovered = decision._ExclusiveLedgerClaim(
                claim_path,
                ledger=ledger,
                stage="deep",
                lock_sha256="b" * 64,
                recover_stale=True,
            )
            recovered.release()

    def test_forbidden_manifest_rejects_nested_producer_smuggling(self) -> None:
        ofen = "k9/10/10/10/10/10/10/10/10/9K[-/-/-/-] w - - 0 1"
        with tempfile.TemporaryDirectory() as directory:
            _, manifest_path = decision._build_synthetic_forbidden_catalog(
                Path(directory), [{"ofen": ofen}]
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["producer"]["hiddenTarget"] = 42
            manifest_path.write_text(
                json.dumps(manifest) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "target/score-like"):
                decision._load_forbidden_catalogs([manifest_path])

    def test_production_forbidden_catalog_is_lexical_and_source_bound(self) -> None:
        ofen = "k9/10/10/10/10/10/10/10/10/9K[-/-/-/-] w - - 0 1"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "prior.jsonl"
            source.write_text(
                json.dumps(
                    {
                        "ofen": ofen,
                        "provenance": {"gameId": "old-game", "runId": "old-run"},
                        "teacherScore": 999999,
                        "outcome": {"targetCp": 888888, "winner": "white"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            catalog = root / "forbidden.jsonl"
            manifest = root / "forbidden.manifest.json"
            real_loads = json.loads

            def guarded_loads(value, *args, **kwargs):
                if str(value).strip() in {"999999", "888888"}:
                    raise AssertionError("a target-bearing numeric value was decoded")
                return real_loads(value, *args, **kwargs)

            with mock.patch.object(decision.json, "loads", side_effect=guarded_loads):
                decision._build_forbidden_catalog_from_sources(
                    [source], catalog, manifest, "2026-07-21T00:00:00Z"
                )

            rows = [row for _, row in decision._jsonl(catalog)]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["sourceGameId"], "old-game")
            self.assertEqual(rows[0]["sourceRunId"], "old-run")
            self.assertEqual(rows[0]["sourceInputSha256"], decision._sha256(source))
            forbidden, pins = decision._load_forbidden_catalogs([manifest])
            exact, _, _ = decision.deep._leakage_keys(ofen)
            self.assertIn(exact, forbidden["exact"])
            self.assertEqual(pins[0]["positionCount"], 1)

            source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "identity changed"):
                decision._load_forbidden_catalogs([manifest])

    def test_forbidden_catalog_is_order_deterministic_and_no_clobber(self) -> None:
        first_ofen = "k9/10/10/10/10/10/10/10/10/9K[-/-/-/-] w - - 0 1"
        second_ofen = "k9/10/10/10/10/10/10/10/9K/10[-/-/-/-] b - - 1 1"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_a = root / "a.json"
            source_b = root / "b.json"
            source_a.write_text(json.dumps({"ofen": first_ofen}), encoding="utf-8")
            source_b.write_text(json.dumps({"ofen": second_ofen}), encoding="utf-8")
            catalog_a = root / "a.catalog.jsonl"
            manifest_a = root / "a.manifest.json"
            catalog_b = root / "b.catalog.jsonl"
            manifest_b = root / "b.manifest.json"
            decision._build_forbidden_catalog_from_sources(
                [source_b, source_a], catalog_a, manifest_a,
                "2026-07-21T00:00:00Z",
            )
            decision._build_forbidden_catalog_from_sources(
                [source_a, source_b], catalog_b, manifest_b,
                "2026-07-21T00:00:00Z",
            )
            self.assertEqual(catalog_a.read_bytes(), catalog_b.read_bytes())
            first_manifest = json.loads(manifest_a.read_text(encoding="utf-8"))
            second_manifest = json.loads(manifest_b.read_text(encoding="utf-8"))
            for value in (first_manifest, second_manifest):
                value["catalog"] = {"normalized": True}
            self.assertEqual(first_manifest, second_manifest)
            with self.assertRaises(FileExistsError):
                decision._build_forbidden_catalog_from_sources(
                    [source_a], catalog_a, manifest_a,
                    "2026-07-21T00:00:00Z",
                )

    def test_forbidden_catalog_rejects_generation4_source_before_scan(self) -> None:
        ofen = "k9/10/10/10/10/10/10/10/10/9K[-/-/-/-] w - - 0 1"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "omega-decision-v1"
            root.mkdir()
            source = root / "prior.jsonl"
            source.write_text(json.dumps({"ofen": ofen}) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "generation-4 namespace"):
                decision._build_forbidden_catalog_from_sources(
                    [source],
                    Path(directory) / "catalog.jsonl",
                    Path(directory) / "manifest.json",
                    "2026-07-21T00:00:00Z",
                )

    def test_g3_projection_requires_and_pins_transitive_manifest(self) -> None:
        ofen = "k9/10/10/10/10/10/10/10/10/9K[-/-/-/-] w - - 0 1"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "g3-projection.jsonl"
            source.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "kind": decision.prior_projection.ROW_KIND,
                        "positionId": "synthetic",
                        "ofen": ofen,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            projection_manifest = root / "g3-projection.manifest.json"
            projection_manifest.write_text("{}\n", encoding="utf-8")
            catalog = root / "forbidden.jsonl"
            manifest = root / "forbidden.manifest.json"

            with self.assertRaisesRegex(ValueError, "strict manifests differ"):
                decision._build_forbidden_catalog_from_sources(
                    [source], catalog, manifest,
                    "2026-07-21T00:00:00Z",
                )

            verified_projection = {
                "manifest": decision._identity(projection_manifest),
                "projection": decision._identity(source),
                "sourceArtifactCount": 3,
                "sourceArtifactBytes": 30,
                "projectedPositionCount": 1,
                "projectionBuilderSource": decision._identity(
                    Path(decision.prior_projection.__file__)
                ),
                "sourceSha256": ["a" * 64, "a" * 64, "b" * 64],
                "historicalGameIds": ["historical-game"],
                "historicalRunIds": ["historical-run"],
            }
            with mock.patch.object(
                decision.prior_projection,
                "verify_projection_manifest",
                return_value=verified_projection,
            ):
                decision._build_forbidden_catalog_from_sources(
                    [source], catalog, manifest,
                    "2026-07-21T00:00:00Z",
                    [projection_manifest],
                )
                forbidden, _pins = decision._load_forbidden_catalogs([manifest])
            exact, _, _ = decision.deep._leakage_keys(ofen)
            self.assertIn(exact, forbidden["exact"])
            self.assertTrue(
                {"a" * 64, "b" * 64}.issubset(forbidden["sourceInputSha256"])
            )
            self.assertIn("historical-game", forbidden["sourceGameIds"])
            self.assertIn("historical-run", forbidden["sourceRunIds"])
            published = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(
                published["sourceProjectionManifests"],
                [decision._identity(projection_manifest)],
            )

    def test_source_suite_provenance_is_closed_and_unique(self) -> None:
        placements = {
            "opening": (
                "krrrrrrrrr/pppppppppp/10/10/10/10/10/10/"
                "PPPPPPPPPP/RRRRRRRRRK[-/-/-/-]"
            ),
            "middlegame": (
                "krrrrrrrrr/pppppppppp/10/10/10/10/10/10/10/"
                "RRRRRRRRRK[-/-/-/-]"
            ),
            "late": (
                "krrrrrrrrr/10/10/10/10/10/10/10/10/"
                "RRRRRRRRRK[-/-/-/-]"
            ),
            "endgame": "krrr6/10/10/10/10/10/10/10/10/6RRRK[-/-/-/-]",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            harness = root / "OmegaMatch.dll"
            sampler = root / "OmegaRootSampler.dll"
            chesslib = root / "ChessLib.dll"
            pool = root / "rules-only-pool.jsonl"
            pool_manifest = root / "rules-only-pool.jsonl.manifest.json"
            pool_seal = root / "rules-only-pool.jsonl.complete.seal.json"
            harness.write_bytes(b"harness")
            sampler.write_bytes(b"sampler")
            chesslib.write_bytes(b"chesslib")
            pool.write_bytes(b"pool")
            pool_manifest.write_bytes(b"manifest")
            pool_seal.write_bytes(b"seal")
            opening_rows = []
            pair_number = 0
            for phase in decision.PHASES:
                for side in ("w", "b"):
                    for ordinal in range(decision.SOURCE_OPENINGS_PER_PHASE_SIDE):
                        pair_number += 1
                        opening_rows.append(
                            {
                                "id": f"{phase}-{side}-{ordinal:04d}",
                                "initialOfen": (
                                    f"{placements[phase]} {side} - - "
                                    f"{ordinal % 90} {pair_number}"
                                ),
                                "moves": [],
                                "source": (
                                    "rules-only-pair:"
                                    f"random-pair-{pair_number:06d}"
                                ),
                            }
                        )
            suite = root / "openings.json"
            suite_value = {
                "schemaVersion": 1,
                "name": "synthetic",
                "rootSamplerSha256": decision._sha256(sampler),
                "rootSamplerChessLibSha256": decision._sha256(chesslib),
                "rootPoolSha256": decision._sha256(pool),
                "rootPoolManifestSha256": decision._sha256(pool_manifest),
                "rootPoolSealSha256": decision._sha256(pool_seal),
                "sourceBuilderSha256": decision._sha256(
                    decision.SOURCE_OPENING_BUILDER
                ),
                "networkFormatSha256": decision._sha256(
                    Path(decision.omega_nnue.__file__)
                ),
                "pythonRuntimeManifestSha256": decision._sha256(
                    decision.runtime_contract.DEFAULT_OUTPUT
                ),
                "crossPairDuplicateExclusion": {
                    "policy": "exclude-all-copies-before-pair-assignment",
                    "distinctOfens": 0,
                    "excludedRows": 0,
                    "affectedTrajectoryPairs": 0,
                    "ofenSetSha256": hashlib.sha256(b"").hexdigest(),
                },
                "openings": opening_rows,
            }
            suite.write_text(json.dumps(suite_value), encoding="utf-8")
            config = root / "source-match.json"
            config.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "runId": "run",
                        "profileId": decision.DATA_PROFILE_ID,
                        "freshnessMarker": "freshness-marker-0001",
                        "seed": decision.SOURCE_SEED,
                        "expectedOpeningSuiteSha256": decision._sha256(suite),
                        "expectedHarnessSha256": decision._sha256(harness),
                        "expectedHarnessBundleSha256": "5" * 64,
                        "engines": [
                            {
                                "id": engine_id,
                                "executable": "senpai.exe",
                                "expectedSha256": "7" * 64,
                                "options": dict(decision.DEFAULT_HCE_OPTIONS),
                            }
                            for engine_id in ("a", "b")
                        ],
                        "match": {
                            "engineA": "a",
                            "engineB": "b",
                            "mode": "nodes",
                            "nodes": decision.SOURCE_NODES,
                            "repeats": 1,
                            "maxPlies": 1,
                            "absoluteMaxPlies": 1,
                            "freshProcessPerGame": True,
                        },
                    }
                ),
                encoding="utf-8",
            )
            source_args = (
                suite,
                config,
                harness,
                sampler,
                chesslib,
                pool,
                pool_manifest,
                pool_seal,
                "7" * 64,
                decision.DATA_PROFILE_ID,
                "freshness-marker-0001",
            )
            with mock.patch.object(decision.subprocess, "run") as verifier:
                contract = decision._load_source_contract(*source_args)
                self.assertEqual(contract["nodes"], decision.SOURCE_NODES)
                self.assertEqual(contract["sourceOpeningProof"]["openingRows"], 6400)
                verifier.assert_called_once_with(
                    decision._source_builder_verify_command(
                        config=config.resolve(),
                        harness=harness.resolve(),
                        root_sampler=sampler.resolve(),
                        root_sampler_chesslib=chesslib.resolve(),
                        root_pool=pool.resolve(),
                        root_pool_manifest=pool_manifest.resolve(),
                        root_pool_seal=pool_seal.resolve(),
                    ),
                    check=True,
                    cwd=str(decision.REPO),
                )

                def mutate_pool(*_args: object, **_kwargs: object) -> None:
                    pool.write_bytes(b"changed-during-verification")

                verifier.side_effect = mutate_pool
                with self.assertRaisesRegex(ValueError, "changed during authenticated"):
                    decision._load_source_contract(*source_args)
                pool.write_bytes(b"pool")
                verifier.side_effect = None

                config_value = json.loads(config.read_text(encoding="utf-8"))
                config_value["match"]["nodes"] = 1999
                config.write_text(json.dumps(config_value), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "2,000-node"):
                    decision._load_source_contract(*source_args)
                config_value["match"]["nodes"] = decision.SOURCE_NODES
                config.write_text(json.dumps(config_value), encoding="utf-8")
                suite_value["openings"][1]["source"] = suite_value["openings"][0][
                    "source"
                ]
                suite.write_text(json.dumps(suite_value), encoding="utf-8")
                config_value["expectedOpeningSuiteSha256"] = decision._sha256(suite)
                config.write_text(json.dumps(config_value), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "malformed or duplicated"):
                    decision._load_source_contract(*source_args)

    def test_source_opening_rows_are_exact_and_phase_balanced(self) -> None:
        placements = {
            "opening": (
                "krrrrrrrrr/pppppppppp/10/10/10/10/10/10/"
                "PPPPPPPPPP/RRRRRRRRRK[-/-/-/-]"
            ),
            "middlegame": (
                "krrrrrrrrr/pppppppppp/10/10/10/10/10/10/10/"
                "RRRRRRRRRK[-/-/-/-]"
            ),
            "late": (
                "krrrrrrrrr/10/10/10/10/10/10/10/10/"
                "RRRRRRRRRK[-/-/-/-]"
            ),
            "endgame": "krrr6/10/10/10/10/10/10/10/10/6RRRK[-/-/-/-]",
        }
        rows = []
        pair = 0
        for phase in decision.PHASES:
            for side in ("w", "b"):
                pair += 1
                rows.append(
                    {
                        "id": f"{phase}-{side}",
                        "initialOfen": f"{placements[phase]} {side} - - 0 {pair}",
                        "moves": [],
                        "source": f"rules-only-pair:random-pair-{pair:06d}",
                    }
                )
        openings, proof = decision._parse_source_openings(
            rows, openings_per_phase_side=1
        )
        self.assertEqual(len(openings), 8)
        self.assertTrue(proof["canonicalOfens"])
        self.assertTrue(proof["exactEmptyMoveArrays"])

        def changed(index: int, field: str, value: object) -> list[dict[str, object]]:
            copy = json.loads(json.dumps(rows))
            copy[index][field] = value
            return copy

        with self.assertRaisesRegex(ValueError, "exact canonical"):
            decision._parse_source_openings(
                changed(0, "initialOfen", rows[0]["initialOfen"].replace(" w ", "  w ")),
                openings_per_phase_side=1,
            )
        with self.assertRaisesRegex(ValueError, "exact canonical"):
            decision._parse_source_openings(
                changed(0, "initialOfen", rows[0]["initialOfen"].replace(" w ", " W ")),
                openings_per_phase_side=1,
            )
        with self.assertRaisesRegex(ValueError, "exact empty moves"):
            decision._parse_source_openings(
                changed(0, "moves", ["a0a1"]), openings_per_phase_side=1
            )
        with self.assertRaisesRegex(ValueError, "provenance tag is malformed"):
            decision._parse_source_openings(
                changed(0, "source", "rules-only-pair:1"),
                openings_per_phase_side=1,
            )
        with self.assertRaisesRegex(ValueError, "malformed or duplicated"):
            decision._parse_source_openings(
                changed(1, "source", rows[0]["source"]),
                openings_per_phase_side=1,
            )
        with self.assertRaisesRegex(ValueError, "duplicate OFEN"):
            decision._parse_source_openings(
                changed(1, "initialOfen", rows[0]["initialOfen"]),
                openings_per_phase_side=1,
            )
        with self.assertRaisesRegex(ValueError, "phase/side quotas"):
            decision._parse_source_openings(
                changed(0, "initialOfen", rows[0]["initialOfen"].replace(" w ", " b ")),
                openings_per_phase_side=1,
            )

    def test_cross_pair_duplicate_exclusion_is_strict(self) -> None:
        empty = {
            "policy": "exclude-all-copies-before-pair-assignment",
            "distinctOfens": 0,
            "excludedRows": 0,
            "affectedTrajectoryPairs": 0,
            "ofenSetSha256": hashlib.sha256(b"").hexdigest(),
        }
        self.assertEqual(
            decision._validate_cross_pair_duplicate_exclusion(empty), empty
        )
        nonempty = {
            **empty,
            "distinctOfens": 1,
            "excludedRows": 2,
            "affectedTrajectoryPairs": 2,
            "ofenSetSha256": "a" * 64,
        }
        self.assertEqual(
            decision._validate_cross_pair_duplicate_exclusion(nonempty), nonempty
        )
        malformed = dict(empty)
        malformed["distinctOfens"] = True
        with self.assertRaisesRegex(ValueError, "counts are malformed"):
            decision._validate_cross_pair_duplicate_exclusion(malformed)
        malformed = dict(empty)
        malformed["ofenSetSha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "empty.*inconsistent"):
            decision._validate_cross_pair_duplicate_exclusion(malformed)
        malformed = dict(empty)
        malformed.pop("excludedRows")
        with self.assertRaisesRegex(ValueError, "wrong field inventory"):
            decision._validate_cross_pair_duplicate_exclusion(malformed)
        malformed = dict(nonempty)
        malformed["affectedTrajectoryPairs"] = 1
        with self.assertRaisesRegex(ValueError, "nonempty.*inconsistent"):
            decision._validate_cross_pair_duplicate_exclusion(malformed)

    def test_source_telemetry_lexically_skips_target_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events = Path(directory) / "events.jsonl"
            events.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "RecordType": "run",
                                "RunId": "r",
                                "Match": {
                                    "Mode": "nodes",
                                    "Nodes": 2000,
                                    "Repeats": 1,
                                    "MaxPlies": 1,
                                    "AbsoluteMaxPlies": 1,
                                    "OutcomeModel": {"secret": 99},
                                },
                                "Engines": [],
                                "TeacherScore": {"secret": 42},
                            }
                        ),
                        json.dumps(
                            {
                                "RecordType": "ply",
                                "GameId": "g",
                                "Attempt": 1,
                                "Ply": 1,
                                "Search": {
                                    "Command": "go nodes 2000",
                                    "ScoreCp": 12345,
                                    "Pv": ["secret"],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "RecordType": "gameResult",
                                "GameId": "g",
                                "Attempt": 1,
                                "Result": "1-0",
                                "Outcome": {"secret": True},
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            records, _ = decision._snapshot_source_events(events)
            run = records[0][1]
            ply = records[1][1]
            result = records[2][1]
            self.assertNotIn("TeacherScore", run)
            self.assertNotIn("OutcomeModel", run["Match"])
            self.assertEqual(ply["SearchCommand"], "go nodes 2000")
            self.assertNotIn("Search", ply)
            self.assertNotIn("Result", result)
            self.assertNotIn("Outcome", result)

    def test_worker_count_is_exactly_four(self) -> None:
        with self.assertRaisesRegex(ValueError, "jobs must be exactly 4"):
            decision.main(
                [
                    "run-shallow",
                    "--input", "missing",
                    "--input-manifest", "missing-manifest",
                    "--ledger", "missing-ledger",
                    "--engine", "missing-engine",
                    "--prelabel-seal", "missing-prelabel",
                    "--preregistration", "missing-prereg",
                    "--final-freeze-seal", "missing-freeze",
                    "--jobs", "3",
                ]
            )

    def test_data_profile_is_distinct_from_experiment_profile(self) -> None:
        common = [
            "prepare-roots",
            "--events", "missing-events",
            "--opening-suite", "missing-suite",
            "--source-match-config", "missing-config",
            "--source-match-harness", "missing-harness",
            "--root-sampler", "missing-sampler",
            "--root-sampler-chesslib", "missing-chesslib",
            "--source-root-pool", "missing-pool",
            "--source-root-pool-manifest", "missing-pool-manifest",
            "--source-root-pool-seal", "missing-pool-seal",
            "--output", "missing-output",
            "--seed", str(decision.ROOT_SELECTION_SEED),
            "--source-seed", str(decision.SOURCE_SEED),
            "--profile-id", decision.PROFILE_ID,
            "--freshness-marker", "freshness-marker-0001",
            "--required-engine-sha256", "a" * 64,
        ]
        with self.assertRaisesRegex(ValueError, "source/profile"):
            decision.main(common + ["--data-profile-id", decision.PROFILE_ID])
        # The correct, separate data profile passes argument validation and
        # reaches the expected missing-file boundary.
        with self.assertRaises(FileNotFoundError):
            decision.main(common + ["--data-profile-id", decision.DATA_PROFILE_ID])

    def test_prepare_roots_requires_all_raw_source_pins(self) -> None:
        arguments = [
            "prepare-roots",
            "--events", "events",
            "--opening-suite", "suite",
            "--source-match-config", "config",
            "--source-match-harness", "harness",
            "--root-sampler", "sampler",
            "--root-sampler-chesslib", "chesslib",
            "--source-root-pool", "pool",
            "--source-root-pool-manifest", "pool-manifest",
            "--source-root-pool-seal", "pool-seal",
            "--output", "roots",
            "--seed", str(decision.ROOT_SELECTION_SEED),
            "--source-seed", str(decision.SOURCE_SEED),
            "--profile-id", decision.PROFILE_ID,
            "--data-profile-id", decision.DATA_PROFILE_ID,
            "--freshness-marker", "freshness-marker-0001",
            "--required-engine-sha256", "a" * 64,
        ]
        parsed = decision._parser().parse_args(arguments)
        self.assertEqual(parsed.source_root_pool, "pool")
        for flag in (
            "--root-sampler-chesslib",
            "--source-root-pool",
            "--source-root-pool-manifest",
            "--source-root-pool-seal",
        ):
            index = arguments.index(flag)
            missing = arguments[:index] + arguments[index + 2 :]
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    decision._parser().parse_args(missing)


if __name__ == "__main__":
    unittest.main()
