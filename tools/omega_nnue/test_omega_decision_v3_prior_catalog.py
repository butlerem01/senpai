#!/usr/bin/env python3
"""Focused authority tests for the G5-to-G6 prior-catalog boundary."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock


TOOL_DIRECTORY = Path(__file__).resolve().parent
if str(TOOL_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOL_DIRECTORY))

import omega_decision_v3_prior_catalog as prior


OFEN_A = "k9/10/10/10/10/10/10/10/10/9K[-/-/-/-] w - - 0 1"
OFEN_B = "k9/10/10/10/10/10/10/10/10/9K[-/-/-/-] b - - 1 1"
OFEN_C = "k9/10/10/10/10/10/10/10/8K1/10[-/-/-/-] w - - 2 2"
BUILD_TIME = "2026-07-24T12:00:00Z"
PLAN_TIME = "2026-07-24T12:00:01.000000Z"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _spec(path: Path, root: Path) -> tuple[str, int, str]:
    payload = path.read_bytes()
    return (
        path.relative_to(root).as_posix(),
        len(payload),
        hashlib.sha256(payload).hexdigest(),
    )


class CatalogFixture:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        (self.root / "build-msvc/data-generation").mkdir(parents=True)
        self.stack = ExitStack()
        self.stack.enter_context(mock.patch.object(prior, "REPO", self.root))

        rows_by_role: dict[str, object] = {
            "sourceEvents": [
                {
                    "gameId": "events-game",
                    "runId": "events-run",
                    "preOfen": OFEN_A,
                    "postOfen": OFEN_B,
                    "teacherScore": {"payload": "SENSITIVE-DO-NOT-DECODE"},
                }
            ],
            "sourceOpeningSuite": {
                "openings": [
                    {
                        "gameId": "opening-game",
                        "runId": "opening-run",
                        "initialOfen": OFEN_C,
                        "result": {"payload": "SENSITIVE-DO-NOT-DECODE"},
                    }
                ]
            },
            "sourceRootPool": [
                {
                    "gameId": "pool-game",
                    "runId": "pool-run",
                    "rootOfen": OFEN_A,
                }
            ],
            "rawRoots": [
                {"sourceGameId": "raw-root-game", "sourceRunId": "raw", "ofen": OFEN_B}
            ],
            "rawChildren": [
                {
                    "sourceGameId": "raw-child-game",
                    "sourceRunId": "raw",
                    "parentOfen": OFEN_A,
                    "childOfen": OFEN_C,
                }
            ],
            "roots": [
                {"sourceGameId": "root-game", "sourceRunId": "kept", "ofen": OFEN_C}
            ],
            "children": [
                {
                    "sourceGameId": "child-game",
                    "sourceRunId": "kept",
                    "parentOfen": OFEN_B,
                    "childOfen": OFEN_A,
                }
            ],
        }

        patched_sources: list[tuple[str, str, int, str]] = []
        for role, relative, _bytes, _sha in prior.G5_SOURCE_SPECS:
            path = self.root / relative
            value = rows_by_role[role]
            if path.suffix == ".json":
                _write_json(path, value)
            else:
                assert isinstance(value, list)
                _write_jsonl(path, value)
            rel, byte_count, digest = _spec(path, self.root)
            patched_sources.append((role, rel, byte_count, digest))
        self.stack.enter_context(
            mock.patch.object(prior, "G5_SOURCE_SPECS", tuple(patched_sources))
        )

        patched_closure: list[tuple[str, str, int, str]] = []
        for role, relative, _bytes, _sha in prior.G5_CLOSURE_SPECS:
            path = self.root / relative
            _write_json(path, {"fixture": role})
            rel, byte_count, digest = _spec(path, self.root)
            patched_closure.append((role, rel, byte_count, digest))
        self.stack.enter_context(
            mock.patch.object(prior, "G5_CLOSURE_SPECS", tuple(patched_closure))
        )

        self.g34_manifest = self.root / prior.G34_MANIFEST_RELATIVE
        _write_json(self.g34_manifest, {"fixture": "G3+G4 manifest"})
        self.stack.enter_context(
            mock.patch.object(prior, "_verify_g34_group", side_effect=self._verify_g34)
        )
        self.stack.enter_context(
            mock.patch.object(
                prior,
                "_authenticated_g5_context",
                side_effect=lambda: prior._declared_g5_context(),
            )
        )

    def close(self) -> None:
        self.stack.close()

    def _verify_g34(self, value: object, *, verify_catalog_hash: bool) -> dict[str, object]:
        del verify_catalog_hash
        if type(value) is dict:
            declared = prior.routing._validate_identity_record(value, "fixture G3+G4 manifest")
            path = Path(declared["path"])
        elif isinstance(value, (str, os.PathLike)):
            declared = None
            path = Path(value)
        else:
            raise ValueError("fixture G3+G4 manifest is malformed")
        actual = prior.routing._identity(path, "fixture G3+G4 manifest")
        if path != self.g34_manifest or (declared is not None and declared != actual):
            raise ValueError("fixture G3+G4 manifest path/identity changed")
        return actual

    @property
    def catalog(self) -> Path:
        return self.root / prior.G5_CATALOG_RELATIVE

    @property
    def manifest(self) -> Path:
        return self.root / prior.G5_MANIFEST_RELATIVE

    def build(self, created_utc: str = BUILD_TIME) -> dict[str, object]:
        return prior.build_catalog(created_utc=created_utc)

    def groups(self, *, g5_as_identity: bool = False) -> list[dict[str, object]]:
        g5: object = (
            prior.routing._identity(self.manifest, "fixture G5 manifest")
            if g5_as_identity
            else str(self.manifest)
        )
        return [
            {"coveredSourceIds": ["G3", "G4"], "manifest": str(self.g34_manifest)},
            {"coveredSourceIds": ["G5"], "manifest": g5},
        ]

    def rewrite_catalog(self, lines: list[bytes]) -> None:
        self.catalog.write_bytes(b"".join(lines))
        document = json.loads(self.manifest.read_text(encoding="utf-8"))
        document["catalog"] = prior.routing._identity(self.catalog, "mutated fixture catalog")
        document["positionCount"] = len(lines)
        _write_json(self.manifest, document)


class PriorCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = CatalogFixture(Path(self.temporary.name) / "repo")

    def tearDown(self) -> None:
        self.fixture.close()
        self.temporary.cleanup()

    def test_build_and_fresh_group_replay_accept_exact_catalog(self) -> None:
        original_loads = prior.g5.json.loads

        def guarded_loads(value: object, *args: object, **kwargs: object) -> object:
            if isinstance(value, str) and "SENSITIVE-DO-NOT-DECODE" in value:
                raise AssertionError("sensitive value lexeme was decoded")
            return original_loads(value, *args, **kwargs)

        with mock.patch.object(prior.g5.json, "loads", side_effect=guarded_loads):
            manifest_identity = self.fixture.build()
            records = prior.verify_catalog_groups(
                self.fixture.groups(g5_as_identity=True),
                full_replay=True,
                plan_created_utc=PLAN_TIME,
            )
        self.assertEqual(records[0]["coveredSourceIds"], ["G3", "G4"])
        self.assertEqual(records[1]["coveredSourceIds"], ["G5"])
        self.assertEqual(records[1]["manifest"], manifest_identity)
        self.assertEqual(
            prior.catalog_group_created_utc(self.fixture.groups(), full_replay=False),
            BUILD_TIME,
        )

    def test_omitted_valid_row_passes_lineage_but_fails_fresh_replay(self) -> None:
        self.fixture.build()
        lines = self.fixture.catalog.read_bytes().splitlines(keepends=True)
        self.assertGreater(len(lines), 1)
        self.fixture.rewrite_catalog(lines[:-1])
        prior.verify_catalog_groups(self.fixture.groups(), full_replay=False)
        with self.assertRaisesRegex(ValueError, "incomplete|fresh lexical replay"):
            prior.verify_catalog_groups(self.fixture.groups(), full_replay=True)

    def test_extra_row_passes_lineage_but_fails_fresh_replay(self) -> None:
        self.fixture.build()
        lines = self.fixture.catalog.read_bytes().splitlines(keepends=True)
        self.fixture.rewrite_catalog([*lines, lines[0]])
        prior.verify_catalog_groups(self.fixture.groups(), full_replay=False)
        with self.assertRaisesRegex(ValueError, "incomplete|fresh lexical replay"):
            prior.verify_catalog_groups(self.fixture.groups(), full_replay=True)

    def test_exact_group_order_path_identity_and_hardlink_are_enforced(self) -> None:
        self.fixture.build()
        groups = self.fixture.groups()
        reversed_groups = [groups[1], groups[0]]
        with self.assertRaisesRegex(ValueError, r"ordered|\[G3,G4\]"):
            prior.verify_catalog_groups(reversed_groups, full_replay=False)

        alias = self.fixture.manifest.with_name("copied.manifest.json")
        alias.write_bytes(self.fixture.manifest.read_bytes())
        copied = self.fixture.groups()
        copied[1]["manifest"] = str(alias)
        with self.assertRaisesRegex(ValueError, "fixed output path"):
            prior.verify_catalog_groups(copied, full_replay=False)

        stale = self.fixture.groups(g5_as_identity=True)
        stale[1]["manifest"] = dict(stale[1]["manifest"])
        stale[1]["manifest"]["bytes"] += 1
        with self.assertRaisesRegex(ValueError, "supplied identity"):
            prior.verify_catalog_groups(stale, full_replay=False)

        hardlink = self.fixture.catalog.with_name("catalog-hardlink.jsonl")
        os.link(self.fixture.catalog, hardlink)
        try:
            with self.assertRaisesRegex(ValueError, "path/type/size|non-linked"):
                prior.verify_catalog_groups(self.fixture.groups(), full_replay=False)
        finally:
            hardlink.unlink()

    def test_catalog_must_follow_g5_freeze_and_precede_plan(self) -> None:
        with self.assertRaisesRegex(ValueError, "follow every bound G5 freeze"):
            self.fixture.build("2026-07-23T19:54:04Z")
        self.assertFalse(self.fixture.catalog.exists())
        self.assertFalse(self.fixture.manifest.exists())

        self.fixture.build()
        with self.assertRaisesRegex(ValueError, "precede the G6 plan"):
            prior.verify_catalog_groups(
                self.fixture.groups(),
                full_replay=False,
                plan_created_utc=BUILD_TIME,
            )
        prior.verify_catalog_groups(
            self.fixture.groups(),
            full_replay=False,
            plan_created_utc=PLAN_TIME,
        )

    def test_authority_sources_are_pinned_before_execution(self) -> None:
        probe_directory = Path(self.temporary.name) / "execution-probe"
        probe_directory.mkdir()
        marker = probe_directory / "executed.txt"
        filename = "substituted.py"
        _routing_filename, expected_bytes, expected_sha256 = prior._EXECUTION_PINS[
            "routing"
        ]
        prefix = (
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('executed')\n#"
        ).encode("utf-8")
        self.assertLess(len(prefix), expected_bytes)
        (probe_directory / filename).write_bytes(
            prefix + b"x" * (expected_bytes - len(prefix))
        )
        pin = (filename, expected_bytes, expected_sha256)
        with (
            mock.patch.object(prior, "TOOL_DIRECTORY", probe_directory),
            mock.patch.dict(
                prior._EXECUTION_PINS, {"substitutionProbe": pin}, clear=False
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "differs from its execution pin"):
                prior._execute_exact_module("substitutionProbe")
        self.assertFalse(marker.exists(), "substituted source executed before authentication")

    def test_public_and_private_preloaded_modules_cannot_substitute_authorities(self) -> None:
        wrapper = Path(prior.__file__).resolve()
        public_names = (
            "omega_decision_teacher_generation5",
            "omega_decision_v3_routing",
            "king_state_generation5_runtime",
            "validate_king_state_v5_preregistration",
            "deep_hce_v2",
            "king_state_generation4_abort",
            "king_state_generation4_prior_projection",
            "omega_nnue",
            "select_screen",
        )
        script = f"""
import importlib.util
import pathlib
import sys
import types
names = {public_names!r}
poison = {{}}
for name in names:
    module = types.ModuleType(name)
    module.poison = True
    poison[name] = module
    sys.modules[name] = module
path = pathlib.Path({str(wrapper)!r})
spec = importlib.util.spec_from_file_location('prior_catalog_attack_probe', path)
loaded = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = loaded
spec.loader.exec_module(loaded)
assert loaded.g5 is not poison['omega_decision_teacher_generation5']
assert loaded.routing is not poison['omega_decision_v3_routing']
assert loaded.g5.runtime_contract is not poison['king_state_generation5_runtime']
assert loaded.g5.v5_prereg is not poison['validate_king_state_v5_preregistration']
assert loaded.g5.deep is not poison['deep_hce_v2']
assert loaded.g5.generation4_abort is not poison['king_state_generation4_abort']
assert loaded.g5.prior_projection is not poison['king_state_generation4_prior_projection']
assert loaded.g5.omega_nnue is not poison['omega_nnue']
assert loaded.g5.select_screen is not poison['select_screen']
assert all(sys.modules[name] is poison[name] for name in names)
"""
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        logical_name = "privatePreloadProbe"
        private_name = prior._PRIVATE_EXECUTION_PREFIX + logical_name
        with mock.patch.dict(
            prior._EXECUTION_PINS,
            {logical_name: prior._EXECUTION_PINS["routing"]},
            clear=False,
        ):
            sys.modules[private_name] = mock.Mock(name="private authority poison")
            try:
                with self.assertRaisesRegex(RuntimeError, "private authority import slot"):
                    prior._execute_exact_module(logical_name)
            finally:
                sys.modules.pop(private_name, None)

    def test_manifest_identity_failure_rolls_back_only_owned_publications(self) -> None:
        original_verify = prior._verify_owned_publication

        def fail_manifest(publication: object, label: str) -> dict[str, object]:
            if "manifest" in label:
                raise RuntimeError("injected manifest identity failure")
            return original_verify(publication, label)

        with mock.patch.object(
            prior, "_verify_owned_publication", side_effect=fail_manifest
        ):
            with self.assertRaisesRegex(RuntimeError, "manifest identity failure"):
                self.fixture.build()
        self.assertFalse(self.fixture.catalog.exists())
        self.assertFalse(self.fixture.manifest.exists())

        foreign_payload = b"concurrent replacement\n"

        def replace_manifest(publication: object, label: str) -> dict[str, object]:
            if "manifest" in label:
                self.fixture.manifest.unlink()
                self.fixture.manifest.write_bytes(foreign_payload)
                raise RuntimeError("injected concurrent replacement")
            return original_verify(publication, label)

        with mock.patch.object(
            prior, "_verify_owned_publication", side_effect=replace_manifest
        ):
            with self.assertRaisesRegex(RuntimeError, "concurrent replacement"):
                self.fixture.build()
        self.assertFalse(self.fixture.catalog.exists())
        self.assertEqual(self.fixture.manifest.read_bytes(), foreign_payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
