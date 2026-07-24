#!/usr/bin/env python3
"""Hostile synthetic tests for the Generation-6 practical launcher."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import king_state_confirmation_generation6_practical_adapter as adapter

# The synthetic executor has no real process shutdown latency.  Preserve every
# other production policy field while avoiding two seconds per fake launch.
adapter.EXECUTION_POLICY = dict(adapter.EXECUTION_POLICY)
adapter.EXECUTION_POLICY["stopGraceMs"] = 0


PINNED_PYTHON = Path(
    r"C:\Users\whate\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
).resolve()


class FakeSuccessor:
    EVENT_KIND = (
        "omega-nnue-generation6-promotion-confirmation-v1-practical-pair-event"
    )

    @staticmethod
    def _sequential_practical(observations, *, attempt_index):
        return {
            "pairCount": len(observations),
            "attemptIndex": attempt_index,
            "decision": "continue" if len(observations) < 128 else "inconclusive",
            "signal": "continue",
        }


def _write(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path.resolve()


def _empty_snapshot():
    return {
        "capturedUtc": "2026-07-24T00:00:00.000001Z",
        "source": "synthetic",
        "relevantProcesses": [],
    }


def _no_rules(context, paths, analysis):
    return {
        "contract": "synthetic no-ply replay",
        "rawEvent": adapter.identity(paths["rawEvents"]),
        "requests": 0,
        "successfulPlies": 0,
        "parityMatches": 0,
        "passes": True,
    }


class Fixture:
    def __init__(self, root: Path, behaviors: list[str] | None = None) -> None:
        self.root = root.resolve()
        self.behaviors = list(behaviors or ["complete"])
        self.main_calls: list[int] = []
        self.candidate_engine = _write(self.root / "candidate/senpai.exe", b"candidate-engine")
        self.incumbent_engine = _write(self.root / "incumbent/senpai.exe", b"incumbent-engine")
        self.candidate_network = _write(self.root / "candidate/g6.nnue", b"g6-network")
        self.incumbent_network = _write(self.root / "incumbent/g5.nnue", b"g5-network")
        self.dotnet = _write(self.root / "runtime/dotnet.exe", b"dotnet")
        self.harness = _write(self.root / "runtime/OmegaMatch.dll", b"harness")
        self.rules = _write(self.root / "runtime/ChessLib.dll", b"rules")
        self.replay = _write(self.root / "runtime/OmegaOpeningPrefixReplay.dll", b"replay")
        self.prereg_path = _write(self.root / "authority/prereg.json", b"{}\n")
        self.claim_path = _write(self.root / "authority/claim.json", b"{}\n")
        self.suite_path = self.root / "authority/suite.json"
        entries = []
        cells = list(adapter.CELLS)
        for index in range(1, 513):
            phase, side = cells[(index - 1) % len(cells)].split(":")
            entries.append(
                {
                    "pairIndex": index,
                    "openingId": f"opening-{index:06d}",
                    "phase": phase,
                    "sideToMove": side,
                    "ofen": f"synthetic-ofen-{index}",
                    "moves": [],
                    "games": ["candidate-white", "candidate-black"],
                }
            )
        self.suite_doc = {
            "attemptIndex": 1,
            "stageSeedCommitment": "2" * 64,
            "entries": entries,
        }
        _write(self.suite_path, adapter._canonical(self.suite_doc))
        self.prereg = {
            "implementation": adapter.identity(adapter.TOOL_PATH),
            "protocol": adapter.identity(adapter.V2_PROTOCOL_PATH),
            "artifactRoot": str(self.root / "artifacts"),
        }
        self.claim = {
            "attemptIndex": 1,
            "entropyCommitment": "1" * 64,
        }
        self.candidate_options = {
            "UCI_Variant": "omega",
            "UCI_Chess960": "false",
            "UseOmegaNNUE": "true",
            "Hash": "64",
            "Threads": "1",
            "Ponder": "false",
            "OmegaNNUEFile": str(self.candidate_network),
        }
        self.incumbent_options = dict(self.candidate_options)
        self.incumbent_options["OmegaNNUEFile"] = str(self.incumbent_network)
        runtime = {
            "protocol": adapter.identity(adapter.V2_PROTOCOL_PATH),
            "dotnetHost": adapter.identity(self.dotnet),
            "omegaMatchAssembly": adapter.identity(self.harness),
            "omegaMatchRulesAssembly": adapter.identity(self.rules),
            "prefixReplayAssembly": adapter.identity(self.replay),
            "prefixReplayRulesAssembly": adapter.identity(self.rules),
            "omegaMatchBundleSha256": "3" * 64,
            "prefixReplayBundleSha256": "4" * 64,
        }
        execution_root = self.root / "artifacts/attempts/attempt-000001/07-practical-execution-v1"
        self.context = adapter.RuntimeContext(
            successor=FakeSuccessor(),
            preregistration_path=self.prereg_path,
            claim_path=self.claim_path,
            suite_path=self.suite_path.resolve(),
            preregistration=self.prereg,
            claim=self.claim,
            suite=self.suite_doc,
            deployment={},
            candidate_engine=adapter.identity(self.candidate_engine),
            candidate_network=adapter.identity(self.candidate_network),
            incumbent_engine=adapter.identity(self.incumbent_engine),
            incumbent_network=adapter.identity(self.incumbent_network),
            candidate_options=self.candidate_options,
            incumbent_options=self.incumbent_options,
            runtime=runtime,
            attempt_index=1,
            execution_root=execution_root,
            ledger_path=execution_root / "practical-events.jsonl",
        )

    def runtime_engine(self, spec, *, wrong_network=False, wrong_options=False):
        network = Path(spec["options"]["OmegaNNUEFile"])
        network_id = adapter.identity(network)
        options = dict(spec["options"])
        if wrong_options:
            options["Hash"] = "65"
        sha = "f" * 64 if wrong_network else network_id["sha256"]
        return {
            "Id": spec["id"],
            "Executable": spec["executable"],
            "Arguments": spec["arguments"],
            "WorkingDirectory": spec["workingDirectory"],
            "Sha256": spec["expectedSha256"],
            "FileSize": Path(spec["executable"]).stat().st_size,
            "LastWriteUtc": "2026-07-24T00:00:00Z",
            "UciName": "Fake Senpai",
            "UciAuthor": "Test",
            "Options": options,
            "ExternalAssets": [
                {
                    "OptionName": "OmegaNNUEFile",
                    "Path": str(network),
                    "Sha256": sha,
                    "FileSize": network_id["bytes"],
                    "LastWriteUtc": "2026-07-24T00:00:00Z",
                }
            ],
            "StartupDiagnostics": [
                f"info string Omega NNUE loaded: fake from {network}",
                "info string Omega NNUE evaluation active",
            ],
            "OmegaNnueActiveVerified": True,
        }

    def game(self, opening, game_index):
        pair_id = f"{opening['id']}-r001"
        if game_index == 1:
            suffix, white, black = "ab", "g6-candidate", "g5-incumbent"
        else:
            suffix, white, black = "ba", "g5-incumbent", "g6-candidate"
        game_id = f"{pair_id}-{suffix}"
        start = {
            "RecordType": "gameStart",
            "GameId": game_id,
            "PairId": pair_id,
            "Attempt": 1,
            "StartedUtc": "2026-07-24T00:00:00Z",
            "OpeningId": opening["id"],
            "WhiteEngineId": white,
            "BlackEngineId": black,
            "InitialOfen": opening["initialOfen"],
            "OpeningMoves": opening["moves"],
        }
        result = {
            "RecordType": "gameResult",
            "GameId": game_id,
            "PairId": pair_id,
            "Attempt": 1,
            "FinishedUtc": "2026-07-24T00:00:01Z",
            "OpeningId": opening["id"],
            "WhiteEngineId": white,
            "BlackEngineId": black,
            "Result": "1/2-1/2",
            "Termination": "synthetic draw",
            "ScoreA": 0.5,
            "Plies": len(opening["moves"]),
            "FinalOfen": opening["initialOfen"],
            "WhiteClockMs": 60_000,
            "BlackClockMs": 60_000,
            "IllegalMoves": 0,
            "IllegalPvs": 0,
            "ProtocolFailures": 0,
            "TimeForfeits": 0,
        }
        return [start, result]

    def executor(self, command, cwd, timeout):
        if "validate" in command:
            return adapter.ProcessOutcome(0, False, b"validated\n", b"")
        action = command[2]
        config_path = Path(command[command.index("--config") + 1])
        config = json.loads(config_path.read_text(encoding="utf-8"))
        suite = json.loads(Path(config["match"]["openingsFile"]).read_text(encoding="utf-8"))
        pair_index = suite["generation6Practical"]["pairIndex"]
        self.main_calls.append(pair_index)
        behavior = self.behaviors.pop(0) if self.behaviors else "complete"
        raw = Path(config["outputDirectory"]) / "events.jsonl"
        raw.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if raw.exists():
            existing = [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines()]
        opening = suite["openings"][0]
        if not existing:
            wrong_network = behavior == "wrong-network"
            wrong_options = behavior == "wrong-options"
            run = {
                "RecordType": "run",
                "RunId": config["runId"],
                "ProfileId": config["profileId"],
                "FreshnessMarker": config["freshnessMarker"],
                "CreatedUtc": "2026-07-24T00:00:00Z",
                "ConfigSha256": adapter.identity(config_path)["sha256"],
                "OpeningSuiteSha256": adapter.identity(Path(config["match"]["openingsFile"]))["sha256"],
                "HarnessVersion": "fake",
                "HarnessSha256": self.context.runtime["omegaMatchAssembly"]["sha256"],
                "HarnessBundleSha256": self.context.runtime["omegaMatchBundleSha256"],
                "OperatingSystem": "fake",
                "Runtime": "fake",
                "ProcessorCount": 1,
                "Seed": config["seed"],
                "Match": copy.deepcopy(config["match"]),
                "Engines": [
                    self.runtime_engine(
                        config["engines"][0],
                        wrong_network=wrong_network,
                        wrong_options=wrong_options,
                    ),
                    self.runtime_engine(config["engines"][1]),
                ],
            }
            existing = [run]
        completed = sum(record.get("RecordType") == "gameResult" for record in existing)
        if behavior == "partial" and completed == 0:
            existing.extend(self.game(opening, 1))
            return_code = 17
        else:
            while completed < 2:
                existing.extend(self.game(opening, completed + 1))
                completed += 1
            return_code = 0
        raw.write_bytes(b"".join(adapter._canonical(record) for record in existing))
        return adapter.ProcessOutcome(return_code, False, f"{action}\n".encode(), b"")


class PracticalAdapterTests(unittest.TestCase):
    def fixture(self, behaviors=None):
        temp = tempfile.TemporaryDirectory(prefix="g6-practical-adapter-test-")
        self.addCleanup(temp.cleanup)
        return Fixture(Path(temp.name), behaviors)

    def run_pair(self, fixture):
        return adapter._run_next_pair_with_dependencies(
            fixture.context,
            executor=fixture.executor,
            snapshotter=_empty_snapshot,
            rules_replay=_no_rules,
        )

    def test_wrong_network_and_wrong_options_fail_closed(self):
        for behavior, category in (
            ("wrong-network", "assetMismatches"),
            ("wrong-options", "diagnosticMismatches"),
        ):
            with self.subTest(behavior=behavior):
                fixture = self.fixture([behavior])
                result = self.run_pair(fixture)
                self.assertEqual(result["state"], "fail-closed-incident")
                self.assertEqual(result["incident"]["category"], category)
                self.assertFalse(result["incident"]["successorEventPublished"])
                self.assertEqual(fixture.context.ledger_path.read_bytes(), b"")

    def test_partial_pair_crash_resumes_without_duplicate_pair(self):
        fixture = self.fixture(["partial", "complete"])
        first = self.run_pair(fixture)
        self.assertEqual(first["state"], "pair-partial-resume-required")
        self.assertEqual(first["completeGames"], 1)
        second = self.run_pair(fixture)
        self.assertEqual(second["state"], "pair-appended")
        records = adapter.validate_ledger(fixture.context)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["pairIndex"], 1)
        self.assertGreaterEqual(records[0]["safety"]["malformedResults"], 1)
        self.assertEqual(fixture.main_calls, [1, 1])

    def test_duplicate_and_tampered_ledger_are_rejected(self):
        for mutation in ("duplicate", "tamper"):
            with self.subTest(mutation=mutation):
                fixture = self.fixture(["complete"])
                self.assertEqual(self.run_pair(fixture)["state"], "pair-appended")
                payload = fixture.context.ledger_path.read_bytes()
                if mutation == "duplicate":
                    fixture.context.ledger_path.write_bytes(payload + payload)
                else:
                    value = json.loads(payload)
                    value["openingId"] = "forged-opening"
                    fixture.context.ledger_path.write_bytes(adapter._canonical(value))
                with self.assertRaises(ValueError):
                    adapter.validate_ledger(fixture.context)

    def test_exact_suite_order_is_never_reordered(self):
        fixture = self.fixture(["complete", "complete", "complete"])
        for expected in (1, 2, 3):
            result = self.run_pair(fixture)
            self.assertEqual(result["state"], "pair-appended")
            self.assertEqual(result["pairIndex"], expected)
        self.assertEqual(fixture.main_calls, [1, 2, 3])
        self.assertEqual(
            [row["pairIndex"] for row in adapter.validate_ledger(fixture.context)],
            [1, 2, 3],
        )

    def test_status_before_start_is_read_only(self):
        fixture = self.fixture()
        result = adapter.status(fixture.context)
        self.assertEqual(result["state"], "not-started")
        self.assertFalse(fixture.context.execution_root.exists())
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn(fixture.claim["entropyCommitment"], serialized)
        self.assertNotIn(fixture.suite_doc["stageSeedCommitment"], serialized)

    def test_execution_transcript_replays_every_sidecar_and_rejects_tamper(self):
        fixture = self.fixture(["partial", *(["complete"] * 8)])
        self.assertEqual(self.run_pair(fixture)["state"], "pair-partial-resume-required")
        for expected in range(1, 9):
            result = self.run_pair(fixture)
            self.assertEqual(result["state"], "pair-appended")
            self.assertEqual(result["pairIndex"], expected)
        transcript = Path(
            adapter._publish_execution_transcript_with_rules_replay(
                fixture.context, rules_replay=_no_rules
            )["path"]
        )
        verified = adapter._verify_execution_transcript_context_with_rules_replay(
            fixture.context, transcript, rules_replay=_no_rules
        )
        self.assertEqual(verified["pairs"], 8)
        self.assertFalse(verified["zeroSafetyFailures"])
        self.assertEqual(len(verified["pairEvidence"]), 8)
        raw = adapter._pair_paths(fixture.context, 1)["rawEvents"]
        raw.write_bytes(raw.read_bytes() + b"{}\n")
        with self.assertRaises((ValueError, adapter.SafetyEvidenceError)):
            adapter._verify_execution_transcript_context_with_rules_replay(
                fixture.context, transcript, rules_replay=_no_rules
            )

    def test_incident_terminal_handoff_is_authenticated_and_no_event_is_fabricated(self):
        fixture = self.fixture(["wrong-network"])
        result = self.run_pair(fixture)
        self.assertEqual(result["state"], "fail-closed-incident")
        handoff = Path(adapter.publish_incident_terminal_handoff(fixture.context)["path"])
        verified = adapter.verify_incident_terminal_handoff_context(
            fixture.context, handoff
        )
        self.assertEqual(verified["requestedSuccessorDecision"], "safety-fail")
        self.assertEqual(verified["requestedGlobalOutcome"], "failed")
        self.assertFalse(verified["successorEventFabricated"])
        incident = Path(verified["incident"]["path"])
        value = json.loads(incident.read_text(encoding="utf-8"))
        value["category"] = "diagnosticMismatches"
        incident.write_bytes(adapter._canonical(value))
        with self.assertRaises(ValueError):
            adapter.verify_incident_terminal_handoff_context(
                fixture.context, handoff
            )

    def test_frozen_managed_replay_helper_accepts_exact_zero_move_endpoints(self):
        fixture = self.fixture(["complete"])
        omega_start = (
            "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/"
            "PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1"
        )
        fixture.suite_doc["entries"][0]["ofen"] = omega_start
        fixture.suite_path.write_bytes(adapter._canonical(fixture.suite_doc))
        fixture.context.runtime.clear()
        fixture.context.runtime.update(adapter._load_v2_runtime())
        result = adapter._run_next_pair_with_dependencies(
            fixture.context,
            executor=fixture.executor,
            snapshotter=_empty_snapshot,
            rules_replay=adapter._managed_rules_replay,
        )
        self.assertEqual(result["state"], "pair-appended")
        completion = adapter._strict_object(
            adapter._pair_paths(fixture.context, 1)["rawCompletion"],
            "managed replay completion",
        )
        self.assertTrue(completion["rulesReplay"]["passes"])
        self.assertEqual(
            completion["rulesReplay"]["openingEndpointParityMatches"], 2
        )

    def test_production_entry_points_reject_dependency_injection_and_pin_successor(self):
        fixture = self.fixture(["complete"])
        with self.assertRaises(TypeError):
            adapter.run_next_pair(fixture.context, executor=fixture.executor)
        with self.assertRaises(TypeError):
            adapter.run_checkpoint(fixture.context, executor=fixture.executor)
        with self.assertRaises(TypeError):
            adapter.publish_execution_transcript(
                fixture.context, rules_replay=_no_rules
            )
        with self.assertRaises(TypeError):
            adapter.verify_execution_transcript_context(
                fixture.context,
                fixture.context.execution_root / "06-execution-transcript.json",
                rules_replay=_no_rules,
            )
        self.assertFalse(fixture.context.execution_root.exists())

        exact_pin = (
            adapter.TOOL_RELATIVE,
            adapter.identity(adapter.TOOL_PATH)["bytes"],
            adapter.identity(adapter.TOOL_PATH)["sha256"],
        )
        adapter._require_reciprocal_successor_pin(
            types.SimpleNamespace(
                PINNED_AUTHORITIES={adapter.SUCCESSOR_PIN_NAME: exact_pin}
            )
        )
        for forged in (
            {},
            {adapter.SUCCESSOR_PIN_NAME: (exact_pin[0], exact_pin[1] + 1, exact_pin[2])},
            {adapter.SUCCESSOR_PIN_NAME: (exact_pin[0], exact_pin[1], "0" * 64)},
        ):
            with self.assertRaises(ImportError):
                adapter._require_reciprocal_successor_pin(
                    types.SimpleNamespace(PINNED_AUTHORITIES=forged)
                )

    def test_timeout_kills_parent_and_child_process_tree(self):
        self.assertEqual(Path(sys.executable).resolve(), PINNED_PYTHON)
        root = Path(tempfile.mkdtemp(prefix="g6-practical-process-test-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        child_pid = root / "child.pid"
        script = root / "tree.py"
        script.write_text(
            "import pathlib,subprocess,sys,time\n"
            "p=subprocess.Popen([sys.executable,'-I','-B','-c','import time; time.sleep(60)'])\n"
            "pathlib.Path(sys.argv[1]).write_text(str(p.pid),encoding='ascii')\n"
            "time.sleep(60)\n",
            encoding="utf-8",
        )
        outcome = adapter._run_subprocess(
            [str(PINNED_PYTHON), "-I", "-B", str(script), str(child_pid)],
            cwd=root,
            timeout_seconds=0.75,
        )
        self.assertTrue(outcome.timed_out)
        deadline = time.time() + 5
        while not child_pid.exists() and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(child_pid.exists())
        pid = int(child_pid.read_text(encoding="ascii"))
        time.sleep(0.25)
        if os.name == "nt":
            system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
            check = subprocess.run(
                [str(system_root / "System32/tasklist.exe"), "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotIn(f'"{pid}"', check.stdout)
        else:
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)


if __name__ == "__main__":
    if Path(sys.executable).resolve() != PINNED_PYTHON:
        raise SystemExit(f"tests require pinned Python: {PINNED_PYTHON}")
    unittest.main(verbosity=2)
