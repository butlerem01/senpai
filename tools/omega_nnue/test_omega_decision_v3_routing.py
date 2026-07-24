#!/usr/bin/env python3
"""Hostile tests for the Generation-6 target-free routing authority."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import random
import tempfile
import types
import unittest
from unittest import mock
import sys

try:
    from . import omega_decision_v3_routing as routing
except ImportError:
    module_directory = str(Path(__file__).resolve().parent)
    if module_directory not in sys.path:
        sys.path.insert(0, module_directory)
    import omega_decision_v3_routing as routing


# Bind the frozen sibling bytes to a private copy so the dependency executed by
# this suite cannot change underneath a running test process.
_TERMINAL_MODULE = Path(routing.__file__).resolve().parent / (
    "omega_decision_v3_terminal_lineage.py"
)
_TERMINAL_PAYLOAD = _TERMINAL_MODULE.read_bytes()
_TERMINAL_SHA256 = hashlib.sha256(_TERMINAL_PAYLOAD).hexdigest()
_TEST_PIN_DIRECTORY = tempfile.TemporaryDirectory(
    prefix="omega-routing-test-terminal-pin-"
)
_TEST_TERMINAL_MODULE = Path(_TEST_PIN_DIRECTORY.name) / _TERMINAL_MODULE.name
_TEST_TERMINAL_MODULE.write_bytes(_TERMINAL_PAYLOAD)
_PRODUCTION_DEPENDENCY_PATH = routing._dependency_path


def _test_dependency_path(name: str, filename: str) -> Path:
    if name == "terminalLineageModule":
        return _TEST_TERMINAL_MODULE
    return _PRODUCTION_DEPENDENCY_PATH(name, filename)


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(routing._canonical_json(value) + b"\n")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_bytes(
        b"".join(routing._canonical_json(row) + b"\n" for row in rows)
    )


def _identity(path: Path) -> dict:
    payload = path.read_bytes()
    return {
        "path": str(path.resolve()),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _placement(piece_count: int, tag: str) -> str:
    if not 2 <= piece_count <= 100:
        raise ValueError(piece_count)
    ranked = sorted(
        range(1, 99),
        key=lambda square: hashlib.sha256(f"{tag}|{square}".encode()).digest(),
    )
    board: dict[int, str] = {0: "K", 99: "k"}
    symbols = "PpNnBbRrQqCcWw"
    for index, square in enumerate(ranked[: piece_count - 2]):
        selector = hashlib.sha256(f"{tag}|piece|{index}".encode()).digest()[0]
        board[square] = symbols[selector % len(symbols)]
    ranks: list[str] = []
    for rank in range(9, -1, -1):
        tokens: list[str] = []
        empty = 0
        for file in range(10):
            symbol = board.get(file * 10 + rank)
            if symbol is None:
                empty += 1
            else:
                if empty:
                    tokens.append(str(empty))
                    empty = 0
                tokens.append(symbol)
        if empty:
            tokens.append(str(empty))
        ranks.append("".join(tokens))
    return "/".join(ranks) + "[-/-/-/-]"


def _ofen(piece_count: int, side: str, tag: str, *, halfmove: int = 0) -> str:
    return f"{_placement(piece_count, tag)} {side} - - {halfmove} 1"


def _child_id(root_id: str, group: str, move: str, ofen: str) -> str:
    return hashlib.sha256(
        f"{routing.CHILD_ID_DOMAIN}\0{root_id}\0{group}\0{move}\0{ofen}".encode()
    ).hexdigest()


def _phase_count(phase: str) -> int:
    return {"opening": 37, "middlegame": 25, "late": 13, "endgame": 7}[phase]


def _source_rows(seed: int, roots_per_split_cell: int = 3) -> tuple[list[dict], list[dict]]:
    roots: list[dict] = []
    children: list[dict] = []
    serial = 0
    for phase in routing.PHASES:
        count = _phase_count(phase)
        for side in routing.SIDES:
            for split in routing.SPLITS:
                found = 0
                probe = 0
                while found < roots_per_split_cell:
                    root_id = f"source-{phase}-{side}-{split}-{probe:05d}"
                    component = routing._component_id([root_id])
                    probe += 1
                    if routing._split_for(component, seed) != split:
                        continue
                    group = f"group-{root_id}"
                    root_ofen = _ofen(count, side, f"root-{serial}-{root_id}")
                    transcript = hashlib.sha256(f"transcript-{root_id}".encode()).hexdigest()
                    roots.append(
                        {
                            "schemaVersion": 1,
                            "kind": routing.ELIGIBLE_ROOT_KIND,
                            "rootId": root_id,
                            "groupId": group,
                            "initialOfen": root_ofen,
                            "moves": [],
                            "plyOfenSha256": [],
                            "rootOfen": root_ofen,
                            "rootOfenSha256": hashlib.sha256(root_ofen.encode()).hexdigest(),
                            "preclassificationTranscriptSha256": transcript,
                            "legalChildCount": 5,
                        }
                    )
                    child_side = "b" if side == "w" else "w"
                    moves = ("a0a1", "b0b1", "c0c1", "d0d1", "e0e1")
                    for ordinal, move in enumerate(moves):
                        child_ofen = _ofen(
                            count,
                            child_side,
                            f"child-{serial}-{root_id}-{ordinal}",
                            halfmove=ordinal,
                        )
                        children.append(
                            {
                                "schemaVersion": 1,
                                "kind": routing.ELIGIBLE_CHILD_KIND,
                                "rootId": root_id,
                                "groupId": group,
                                "initialOfen": root_ofen,
                                "moves": [],
                                "plyOfenSha256": [],
                                "parentOfen": root_ofen,
                                "parentOfenSha256": hashlib.sha256(root_ofen.encode()).hexdigest(),
                                "childId": _child_id(root_id, group, move, child_ofen),
                                "move": move,
                                "moveOrdinal": ordinal,
                                "childOfen": child_ofen,
                                "childOfenSha256": hashlib.sha256(child_ofen.encode()).hexdigest(),
                                "classification": "child-nonterminal",
                                "preclassificationTranscriptSha256": transcript,
                            }
                        )
                    found += 1
                    serial += 1
    roots.sort(key=lambda row: row["rootId"])
    root_order = {row["rootId"]: index for index, row in enumerate(roots)}
    children.sort(key=lambda row: (root_order[row["rootId"]], row["moveOrdinal"]))
    return roots, children


class Fixture:
    SEED = 2026072403
    CREATED = "2026-07-24T00:00:02.000000Z"
    TERMINAL_CREATED = "2026-07-24T00:00:01.000000Z"
    MINI_QUOTAS = {"train": 1, "validation": 1, "heldOut": 1}

    def __init__(self, root: Path) -> None:
        self.root = root
        self.roots_path = root / "eligible-roots.jsonl"
        self.children_path = root / "eligible-children.jsonl"
        self.terminal_path = root / "terminal-lineage.json"
        self.routing_path = root / "routing.jsonl"
        self.component_path = root / "components.jsonl"
        self.root_manifest_path = root / "source-roots.manifest.json"
        self.child_manifest_path = root / "source-children.manifest.json"
        self.completion_path = root / "routing.completion.json"
        self.catalog_path = root / "prior.positions.jsonl"
        self.catalog_manifest_path = root / "prior.positions.manifest.json"
        self.prior_source = root / "prior-source.jsonl"
        self.prior_producer = root / "prior-producer.bin"
        self.roots, self.children = _source_rows(self.SEED)
        _write_jsonl(self.roots_path, self.roots)
        _write_jsonl(self.children_path, self.children)
        _write_json(self.terminal_path, {"synthetic": "terminal-lineage-identity"})
        self._forbidden()

    def terminal_document(self) -> dict:
        return {
            "eligibleRoots": _identity(self.roots_path),
            "eligibleChildren": _identity(self.children_path),
            "targetFieldsDecodedAtCompletion": 0,
            "resultInformationRead": False,
            "finalStageSeal": True,
            "createdUtc": self.TERMINAL_CREATED,
        }

    def _forbidden(self) -> None:
        _write_jsonl(self.prior_source, [{"ofen": _ofen(8, "w", "prior-source")}])
        self.prior_producer.write_bytes(b"synthetic target-opaque prior producer\n")
        source_id = _identity(self.prior_source)
        prior_ofen = _ofen(8, "b", "forbidden-position")
        exact, orbit, signatures = routing._leakage_keys(prior_ofen)
        row = {
            "schemaVersion": 1,
            "kind": routing.FORBIDDEN_ROW_KIND,
            "ofen": prior_ofen,
            "exactPositionKey": exact,
            "conservativeOrbitKey": orbit,
            "conservativeOrbitSignatures": list(signatures),
            "sourceArtifactSha256": source_id["sha256"],
        }
        row["positionId"] = routing._catalog_position_id(row)
        _write_jsonl(self.catalog_path, [row])
        empty_digest = routing._digest([])
        manifest = {
            "schemaVersion": 1,
            "kind": routing.FORBIDDEN_MANIFEST_KIND,
            "targetOpaque": True,
            "createdUtc": "2026-07-23T00:00:00Z",
            "catalog": _identity(self.catalog_path),
            "catalogSchema": {
                "recognizedFields": sorted(routing.FORBIDDEN_ROW_FIELDS),
                "unknownFieldPolicy": "abort",
                "minimumPositionIdentity": (
                    "at least one exact or conservative orbit signature per row"
                ),
            },
            "sourceInventory": [source_id],
            "sourceInventorySha256": routing._digest([source_id]),
            "sourceProjectionManifests": [],
            "sourceProjectionManifestsSha256": empty_digest,
            "priorSourceAudits": [],
            "priorSourceAuditsSha256": empty_digest,
            "extractionPolicy": {"policyId": "synthetic-target-opaque-test-v1"},
            "positionCount": 1,
            "targetOrScoreFieldsDecoded": 0,
            "targetOrScoreFieldsEmitted": 0,
            "producer": _identity(self.prior_producer),
        }
        _write_json(self.catalog_manifest_path, manifest)

    def materialize(self) -> dict:
        return routing._materialize(
            eligible_roots=self.roots_path,
            eligible_children=self.children_path,
            terminal_lineage=self.terminal_path,
            prior_forbidden_manifests=[self.catalog_manifest_path],
            routing_output=self.routing_path,
            component_output=self.component_path,
            source_root_manifest_output=self.root_manifest_path,
            source_children_manifest_output=self.child_manifest_path,
            completion_output=self.completion_path,
            seed=self.SEED,
            created_utc=self.CREATED,
            quotas=self.MINI_QUOTAS,
        )

    def patch_terminal(self):
        return mock.patch.object(
            routing,
            "_verify_terminal_lineage",
            side_effect=lambda _: self.terminal_document(),
        )


class RoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.original_terminal_bytes = routing.EXPECTED_TERMINAL_LINEAGE_MODULE_BYTES
        cls.original_terminal_sha256 = routing.EXPECTED_TERMINAL_LINEAGE_MODULE_SHA256
        routing.EXPECTED_TERMINAL_LINEAGE_MODULE_BYTES = len(_TERMINAL_PAYLOAD)
        routing.EXPECTED_TERMINAL_LINEAGE_MODULE_SHA256 = _TERMINAL_SHA256
        cls.dependency_path_patch = mock.patch.object(
            routing, "_dependency_path", side_effect=_test_dependency_path
        )
        cls.dependency_path_patch.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.dependency_path_patch.stop()
        routing.EXPECTED_TERMINAL_LINEAGE_MODULE_BYTES = cls.original_terminal_bytes
        routing.EXPECTED_TERMINAL_LINEAGE_MODULE_SHA256 = cls.original_terminal_sha256
        routing._DEPENDENCY_CACHE.pop("terminalLineageModule", None)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _parsed(self, fixture: Fixture):
        roots, _ = routing._parse_roots(fixture.roots_path)
        children, _ = routing._parse_children(fixture.children_path, roots)
        forbidden = routing._load_forbidden([fixture.catalog_manifest_path])
        return roots, children, forbidden

    def test_phase_boundaries_are_recomputed_from_root_ofen(self) -> None:
        expected = ((37, "opening"), (36, "middlegame"), (25, "middlegame"),
                    (24, "late"), (13, "late"), (12, "endgame"), (7, "endgame"))
        for count, phase in expected:
            actual, side, pieces = routing._phase_side(
                _ofen(count, "w", f"phase-{count}"), f"phase-{count}"
            )
            self.assertEqual((actual, side, pieces), (phase, "w", count))
        with self.assertRaisesRegex(ValueError, "fewer than seven"):
            routing._phase_side(_ofen(6, "w", "six"), "bad")

    def test_source_group_and_shared_orbit_join_before_filtering(self) -> None:
        def root(root_id: str, group: str, ofen: str, transcript: str) -> routing.Root:
            phase, side, count = routing._phase_side(ofen, root_id)
            exact, orbit, signatures = routing._leakage_keys(ofen)
            return routing.Root(
                root_id, group, ofen, (), (), ofen,
                routing._sha256_bytes(ofen.encode()), transcript * 64, 4,
                phase, side, count, exact, orbit, signatures,
            )

        group_a = _ofen(13, "w", "group-a")
        group_b = _ofen(13, "w", "group-b")
        orbit_a = _ofen(13, "w", "orbit-a")
        mirrored = routing.transformed_observable(
            routing.observable_ofen(orbit_a), True, False
        ) + " - 0 1"
        roots = [
            root("a", "same-group", group_a, "1"),
            root("b", "same-group", group_b, "2"),
            root("c", "third-group", orbit_a, "3"),
            root("d", "fourth-group", mirrored, "4"),
        ]
        components, members = routing._build_components(roots, [])
        self.assertEqual(components["a"], components["b"])
        self.assertEqual(components["c"], components["d"])
        self.assertNotEqual(roots[2].exact, roots[3].exact)
        self.assertEqual(roots[2].orbit, roots[3].orbit)
        self.assertEqual(set(members[components["a"]]), {"a", "b"})
        self.assertEqual(set(members[components["c"]]), {"c", "d"})

    def test_streamed_component_digests_match_canonical_reference(self) -> None:
        members = {
            "component-b": ("root-3",),
            "component-a": ("root-1", "root-2"),
        }
        splits = {"component-a": "train", "component-b": "heldOut"}
        forbidden = {"component-a": False, "component-b": True}
        membership_rows = [
            {"leakageComponentId": component, "sourceRootIds": list(values)}
            for component, values in sorted(members.items())
        ]
        assignment_rows = [
            {
                "leakageComponentId": component,
                "split": splits[component],
                "forbidden": forbidden[component],
            }
            for component in sorted(members)
        ]
        self.assertEqual(
            routing._component_membership_digest(members),
            routing._digest(membership_rows),
        )
        self.assertEqual(
            routing._component_assignment_digest(members, splits, forbidden),
            routing._digest(assignment_rows),
        )
        expected_component = "component-" + hashlib.sha256(
            routing.COMPONENT_DOMAIN.encode("utf-8")
            + b"\0"
            + routing._canonical_json(["root-1", "root-2"])
        ).hexdigest()
        self.assertEqual(
            routing._component_id(("root-2", "root-1")), expected_component
        )

    def test_order_and_worker_independent_computation(self) -> None:
        fixture = Fixture(self.root)
        roots, children, forbidden = self._parsed(fixture)
        first = routing._compute(
            roots, children, forbidden, seed=fixture.SEED, quotas=fixture.MINI_QUOTAS
        )
        shuffled_roots = list(roots)
        shuffled_children = list(children)
        random.Random(17).shuffle(shuffled_roots)
        random.Random(23).shuffle(shuffled_children)
        second = routing._compute(
            shuffled_roots, shuffled_children, forbidden,
            seed=fixture.SEED, quotas=fixture.MINI_QUOTAS,
        )
        self.assertEqual(first.routing_rows, second.routing_rows)
        self.assertEqual(first.component_rows, second.component_rows)
        self.assertEqual(first.digests, second.digests)

    def test_quota_insufficiency_fails_closed(self) -> None:
        fixture = Fixture(self.root)
        roots, children, forbidden = self._parsed(fixture)
        with self.assertRaisesRegex(ValueError, "quota insufficiency"):
            routing._compute(
                roots[:1], children[:5], forbidden,
                seed=fixture.SEED, quotas=fixture.MINI_QUOTAS,
            )

    def test_forbidden_overlap_excludes_entire_component(self) -> None:
        fixture = Fixture(self.root)
        roots, children, forbidden = self._parsed(fixture)
        first = roots[0]
        mate = roots[1]
        # Join the two roots by source group without changing a target or score.
        joined = [first, routing.Root(
            mate.root_id, first.group_id, mate.initial_ofen, mate.moves, mate.ply_hashes,
            mate.ofen, mate.ofen_sha256, mate.transcript_sha256, mate.legal_child_count,
            mate.phase, mate.side, mate.piece_count, mate.exact, mate.orbit, mate.signatures,
        ), *roots[2:]]
        overlapping = routing.Forbidden(
            frozenset({first.exact}), frozenset(), forbidden.manifests,
            forbidden.catalogs, forbidden.positions, forbidden.source_artifact_sha256,
        )
        computed = routing._compute(
            joined, children, overlapping, seed=fixture.SEED,
            quotas=fixture.MINI_QUOTAS,
        )
        selected = {row["sourceRootId"] for row in computed.routing_rows}
        self.assertNotIn(first.root_id, selected)
        self.assertNotIn(mate.root_id, selected)
        self.assertTrue(computed.component_forbidden[computed.root_components[first.root_id]])
        self.assertEqual(computed.forbidden_authority["selectedExactPositionOverlaps"], 0)
        self.assertEqual(
            computed.forbidden_authority["selectedConservativeSignatureOverlaps"], 0
        )

    def test_prior_source_artifact_reuse_fails_closed(self) -> None:
        fixture = Fixture(self.root)
        roots, children, forbidden = self._parsed(fixture)
        roots_identity = _identity(fixture.roots_path)
        children_identity = _identity(fixture.children_path)
        reused = routing.Forbidden(
            forbidden.exact, forbidden.signatures, forbidden.manifests,
            forbidden.catalogs, forbidden.positions,
            frozenset({roots_identity["sha256"]}),
        )
        with self.assertRaisesRegex(ValueError, "source artifacts overlap"):
            routing._assert_source_artifact_disjoint(
                roots_identity, children_identity, reused
            )

    def test_manifest_last_publication_and_no_clobber(self) -> None:
        fixture = Fixture(self.root)
        with fixture.patch_terminal():
            completion = fixture.materialize()
            before = {path: path.read_bytes() for path in (
                fixture.routing_path, fixture.component_path, fixture.root_manifest_path,
                fixture.child_manifest_path, fixture.completion_path,
            )}
            self.assertTrue(completion["finalStageSeal"])
            with self.assertRaises(FileExistsError):
                fixture.materialize()
            self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_phase_relabel_is_rejected_by_fresh_replay(self) -> None:
        fixture = Fixture(self.root)
        with fixture.patch_terminal():
            fixture.materialize()
            rows = [json.loads(line) for line in fixture.routing_path.read_text().splitlines()]
            rows[0]["phase"] = "endgame" if rows[0]["phase"] != "endgame" else "opening"
            _write_jsonl(fixture.routing_path, rows)
            completion = json.loads(fixture.completion_path.read_text())
            completion["targetFreeRouting"] = _identity(fixture.routing_path)
            _write_json(fixture.completion_path, completion)
            with self.assertRaisesRegex(ValueError, "recomputation"):
                routing._verify_completion(
                    fixture.completion_path, expected_quotas=fixture.MINI_QUOTAS
                )

    def test_component_group_split_is_rejected(self) -> None:
        fixture = Fixture(self.root)
        with fixture.patch_terminal():
            fixture.materialize()
            rows = [json.loads(line) for line in fixture.component_path.read_text().splitlines()]
            rows[1]["sourceGroupId"] = rows[0]["sourceGroupId"]
            rows[1]["split"] = next(
                split for split in routing.SPLITS if split != rows[0]["split"]
            )
            _write_jsonl(fixture.component_path, rows)
            with self.assertRaisesRegex(ValueError, "source group crosses"):
                routing._parse_component_output(fixture.component_path)

    def test_child_substitution_is_rejected_by_fresh_replay(self) -> None:
        fixture = Fixture(self.root)
        with fixture.patch_terminal():
            fixture.materialize()
            rows = [json.loads(line) for line in fixture.routing_path.read_text().splitlines()]
            rows[0]["children"][0], rows[1]["children"][0] = (
                rows[1]["children"][0], rows[0]["children"][0]
            )
            _write_jsonl(fixture.routing_path, rows)
            completion = json.loads(fixture.completion_path.read_text())
            completion["targetFreeRouting"] = _identity(fixture.routing_path)
            _write_json(fixture.completion_path, completion)
            with self.assertRaisesRegex(ValueError, "recomputation"):
                routing._verify_completion(
                    fixture.completion_path, expected_quotas=fixture.MINI_QUOTAS
                )

    def test_hardlink_inputs_are_rejected(self) -> None:
        fixture = Fixture(self.root)
        hardlink = self.root / "roots-hardlink.jsonl"
        os.link(fixture.roots_path, hardlink)
        with self.assertRaisesRegex(ValueError, "non-linked regular file"):
            routing._parse_roots(fixture.roots_path)
        hardlink.unlink()

    def test_reparse_inputs_are_rejected_before_open(self) -> None:
        fixture = Fixture(self.root)
        symlink = self.root / "roots-symlink.jsonl"
        try:
            os.symlink(fixture.roots_path, symlink)
        except OSError:
            real_lstat = os.lstat

            class ReparseMetadata:
                def __init__(self, wrapped):
                    self._wrapped = wrapped
                    self.st_file_attributes = routing._FILE_ATTRIBUTE_REPARSE_POINT

                def __getattr__(self, name):
                    return getattr(self._wrapped, name)

            def marked(path):
                metadata = real_lstat(path)
                if Path(path) == fixture.roots_path:
                    return ReparseMetadata(metadata)
                return metadata

            with mock.patch.object(routing.os, "lstat", side_effect=marked):
                with self.assertRaisesRegex(ValueError, "non-linked regular file"):
                    routing._parse_roots(fixture.roots_path)
        else:
            with self.assertRaisesRegex(ValueError, "non-linked regular file"):
                routing._parse_roots(symlink)

    def test_target_like_catalog_field_is_rejected_without_decoding(self) -> None:
        fixture = Fixture(self.root)
        rows = [json.loads(line) for line in fixture.catalog_path.read_text().splitlines()]
        rows[0]["deepScoreCp"] = 123
        _write_jsonl(fixture.catalog_path, rows)
        manifest = json.loads(fixture.catalog_manifest_path.read_text())
        manifest["catalog"] = _identity(fixture.catalog_path)
        _write_json(fixture.catalog_manifest_path, manifest)
        with self.assertRaisesRegex(ValueError, "undeclared fields|target/score-like"):
            routing._load_forbidden([fixture.catalog_manifest_path])

    def test_target_value_lexeme_is_never_decoded(self) -> None:
        fixture = Fixture(self.root)
        valid = fixture.catalog_path.read_text().strip()
        payload = valid[:-1] + ',"deepScoreCp":THIS_IS_NOT_JSON}\n'
        fixture.catalog_path.write_text(payload, encoding="utf-8")
        manifest = json.loads(fixture.catalog_manifest_path.read_text())
        manifest["catalog"] = _identity(fixture.catalog_path)
        _write_json(fixture.catalog_manifest_path, manifest)
        with self.assertRaisesRegex(ValueError, "target/score-like key 'deepScoreCp'"):
            routing._load_forbidden([fixture.catalog_manifest_path])

    def test_embedded_prior_provenance_identity_is_rehashed(self) -> None:
        fixture = Fixture(self.root)
        projection = self.root / "prior-projection.manifest.json"
        _write_json(projection, {"kind": "synthetic-prior-projection"})
        manifest = json.loads(fixture.catalog_manifest_path.read_text())
        manifest["sourceProjectionManifests"] = [_identity(projection)]
        manifest["sourceProjectionManifestsSha256"] = routing._digest(
            manifest["sourceProjectionManifests"]
        )
        _write_json(fixture.catalog_manifest_path, manifest)
        routing._load_forbidden([fixture.catalog_manifest_path])
        projection.write_bytes(b"mutated after manifest publication\n")
        with self.assertRaisesRegex(ValueError, "differs from its content"):
            routing._load_forbidden([fixture.catalog_manifest_path])

    def test_child_parent_substitution_fails_structural_cross_link(self) -> None:
        fixture = Fixture(self.root)
        rows = copy.deepcopy(fixture.children)
        rows[0]["parentOfen"] = rows[5]["parentOfen"]
        rows[0]["parentOfenSha256"] = rows[5]["parentOfenSha256"]
        _write_jsonl(fixture.children_path, rows)
        roots, _ = routing._parse_roots(fixture.roots_path)
        with self.assertRaisesRegex(ValueError, "parent differs"):
            routing._parse_children(fixture.children_path, roots)

    def test_forbidden_catalog_uses_bounded_stream_not_whole_file_loader(self) -> None:
        fixture = Fixture(self.root)
        with mock.patch.object(
            routing,
            "_load_jsonl",
            side_effect=AssertionError("whole-file JSONL load"),
            create=True,
        ):
            forbidden = routing._load_forbidden([fixture.catalog_manifest_path])
        try:
            row = json.loads(fixture.catalog_path.read_text())
            self.assertIn(row["exactPositionKey"], forbidden.exact)
            self.assertGreater(forbidden.disk_bytes, 0)
        finally:
            forbidden.close()

    def test_forbidden_catalog_flushes_on_accumulated_key_bound(self) -> None:
        fixture = Fixture(self.root)
        source_sha = _identity(fixture.prior_source)["sha256"]

        def row(game_id: str, signatures: list[str]) -> dict:
            value = {
                "schemaVersion": 1,
                "kind": routing.FORBIDDEN_ROW_KIND,
                "exactPositionKey": "",
                "conservativeOrbitKey": "",
                "conservativeOrbitSignatures": signatures,
                "sourceGameId": game_id,
                "sourceArtifactSha256": source_sha,
            }
            value["positionId"] = routing._catalog_position_id(value)
            return value

        signatures = [f"{number:064x}" for number in range(1, 5)]
        _write_jsonl(
            fixture.catalog_path,
            [row("many-signatures", signatures), row("one-signature", ["f" * 64])],
        )
        manifest = json.loads(fixture.catalog_manifest_path.read_text())
        manifest["catalog"] = _identity(fixture.catalog_path)
        manifest["positionCount"] = 2
        _write_json(fixture.catalog_manifest_path, manifest)

        batch_sizes: list[tuple[int, int]] = []
        original = routing._ForbiddenKeyStore.add_batch

        def measured(store, position_ids, exact_keys, signature_keys, *, label):
            batch_sizes.append((len(position_ids), len(signature_keys)))
            return original(
                store, position_ids, exact_keys, signature_keys, label=label
            )

        with mock.patch.object(
            routing, "MAX_FORBIDDEN_BATCH_KEY_ENTRIES", 4
        ), mock.patch.object(routing._ForbiddenKeyStore, "add_batch", new=measured):
            forbidden = routing._load_forbidden([fixture.catalog_manifest_path])
        try:
            self.assertEqual(batch_sizes, [(1, 4), (1, 1)])
            self.assertEqual(len(forbidden.signatures), 5)
        finally:
            forbidden.close()

    def test_bounded_graph_matches_in_memory_reference_bytes(self) -> None:
        fixture = Fixture(self.root)
        roots, children, forbidden = self._parsed(fixture)
        reference = routing._compute(
            roots,
            children,
            forbidden,
            seed=fixture.SEED,
            quotas=fixture.MINI_QUOTAS,
        )
        graph, identity = routing._load_bounded_children(
            fixture.children_path, roots, forbidden
        )
        try:
            bounded = routing._compute_bounded(
                roots,
                graph,
                forbidden,
                seed=fixture.SEED,
                quotas=fixture.MINI_QUOTAS,
            )
            self.assertEqual(identity, _identity(fixture.children_path))
            self.assertEqual(bounded.routing_rows, reference.routing_rows)
            self.assertEqual(bounded.component_rows, reference.component_rows)
            self.assertEqual(bounded.coverage, reference.coverage)
            self.assertEqual(bounded.digests, reference.digests)
            self.assertEqual(
                bounded.forbidden_authority, reference.forbidden_authority
            )
            self.assertEqual(bounded.all_children, ())
            self.assertGreater(graph.disk_bytes, 0)
            common = {
                "created_utc": fixture.CREATED,
                "terminal_lineage": {},
                "eligible_roots": {},
                "eligible_children": {},
                "source_root_manifest": {},
                "producer": {},
                "dependencies": {},
                "roots": roots,
            }
            reference_manifest = routing._child_manifest_document(
                **common, children=children
            )
            bounded_manifest = routing._child_manifest_document(
                **common, children=None, child_inventory=graph.inventory
            )
            self.assertEqual(bounded_manifest, reference_manifest)
        finally:
            graph.close()

    def test_production_materialization_never_calls_list_child_parser(self) -> None:
        fixture = Fixture(self.root)
        with fixture.patch_terminal(), mock.patch.object(
            routing,
            "_parse_children",
            side_effect=AssertionError("unbounded child parser called"),
        ):
            completion = fixture.materialize()
        self.assertEqual(completion["coverage"]["eligibleChildren"], len(fixture.children))

    def test_root_and_reference_child_parsers_stream_descriptors(self) -> None:
        fixture = Fixture(self.root)
        with mock.patch.object(
            routing,
            "_load_jsonl",
            side_effect=AssertionError("whole-file JSONL load"),
            create=True,
        ):
            roots, _ = routing._parse_roots(fixture.roots_path)
            children, _ = routing._parse_children(fixture.children_path, roots)
        self.assertEqual((len(roots), len(children)), (len(fixture.roots), len(fixture.children)))

    def test_resident_root_inventory_has_reviewed_hard_ceiling(self) -> None:
        fixture = Fixture(self.root)
        with mock.patch.object(routing, "MAX_ELIGIBLE_ROOTS", 1):
            with self.assertRaisesRegex(ValueError, "bounded-memory ceiling"):
                routing._parse_roots(fixture.roots_path)

    def test_dependency_substitution_is_rejected_before_execution(self) -> None:
        source = Path(routing.__file__).resolve().parent / "omega_nnue.py"
        substitute = self.root / "omega_nnue.py"
        payload = bytearray(source.read_bytes())
        payload[-1] ^= 1
        substitute.write_bytes(payload)
        with self.assertRaisesRegex(RuntimeError, "differs from its execution pin"):
            routing._execute_pinned_module(
                logical_name="omegaNnue",
                path=substitute,
                expected_bytes=routing.EXPECTED_OMEGA_NNUE_BYTES,
                expected_sha256=routing.EXPECTED_OMEGA_NNUE_SHA256,
            )

    def test_unfinalized_dependency_pin_fails_closed(self) -> None:
        dependency = self.root / "dependency.py"
        dependency.write_bytes(b"VALUE = 1\n")
        with self.assertRaisesRegex(RuntimeError, "execution pin is not finalized"):
            routing._execute_pinned_module(
                logical_name="unfinalizedDependency",
                path=dependency,
                expected_bytes=None,
                expected_sha256=None,
            )

    def test_executed_dependency_snapshot_matches_reported_identity(self) -> None:
        dependency = self.root / "dependency.py"
        payload = b"VALUE = 'executed-from-pinned-snapshot'\n"
        dependency.write_bytes(payload)
        loaded = routing._execute_pinned_module(
            logical_name="syntheticDependency",
            path=dependency,
            expected_bytes=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
        )
        self.assertEqual(loaded.module.VALUE, "executed-from-pinned-snapshot")
        self.assertEqual(loaded.identity, _identity(dependency))
        dependency.write_bytes(b"VALUE = 'tampered-after-load'\n")
        with self.assertRaisesRegex(ValueError, "differs from its content"):
            routing._verify_identity_record(loaded.identity, "loaded dependency")

    def test_select_snapshot_cannot_import_sys_modules_substitute(self) -> None:
        directory = Path(routing.__file__).resolve().parent
        omega = routing._execute_pinned_module(
            logical_name="omegaNnue",
            path=directory / "omega_nnue.py",
            expected_bytes=routing.EXPECTED_OMEGA_NNUE_BYTES,
            expected_sha256=routing.EXPECTED_OMEGA_NNUE_SHA256,
        )
        substitute = types.ModuleType("omega_nnue")

        def fail(*_args, **_kwargs):
            raise AssertionError("ordinary sys.modules substitute was imported")

        substitute.parse_ofen = fail
        substitute._active_features_from_parsed = fail
        with mock.patch.dict(sys.modules, {"omega_nnue": substitute}):
            select = routing._execute_pinned_module(
                logical_name="selectScreen",
                path=directory / "select_screen.py",
                expected_bytes=routing.EXPECTED_SELECT_SCREEN_BYTES,
                expected_sha256=routing.EXPECTED_SELECT_SCREEN_SHA256,
                pinned_imports={"omega_nnue": omega.module},
            )
            observed = select.module.observable_ofen(_ofen(13, "w", "pinned-import"))
        self.assertEqual(observed.split()[1:], ["w", "-"])

    def test_pinned_select_unbounded_caches_are_drained_per_position(self) -> None:
        for index in range(12):
            routing._leakage_keys(_ofen(13, "w", f"cache-drain-{index}"))
        module = routing._pinned_dependency("selectScreen").module
        for name in (
            "transformed_observable",
            "canonical_observable",
            "nnue_observable_key",
            "observable_symmetries",
            "identity_key",
            "input_keys",
        ):
            function = getattr(module, name)
            self.assertEqual(function.cache_info().currsize, 0, name)


if __name__ == "__main__":
    unittest.main()
