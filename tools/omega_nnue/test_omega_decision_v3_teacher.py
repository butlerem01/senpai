#!/usr/bin/env python3
"""Focused lifecycle and UCI tests for the Generation-6 teacher authority."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock


try:
    from . import omega_decision_v3_teacher as teacher
except ImportError:
    module_directory = str(Path(__file__).resolve().parent)
    if module_directory not in sys.path:
        sys.path.insert(0, module_directory)
    import omega_decision_v3_teacher as teacher


FAKE_PROJECT = """
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net9.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
    <AssemblyName>SenpaiFakeG6</AssemblyName>
    <UseAppHost>true</UseAppHost>
  </PropertyGroup>
</Project>
"""

FAKE_SOURCE = r"""
using System.Globalization;
using System.Security.Cryptography;
using System.Text;

static Dictionary<string, string> ReadMap(string path)
{
    var result = new Dictionary<string, string>(StringComparer.Ordinal);
    if (!File.Exists(path)) return result;
    foreach (var raw in File.ReadAllLines(path))
    {
        var split = raw.IndexOf('=');
        if (split > 0) result[raw[..split].Trim()] = raw[(split + 1)..].Trim();
    }
    return result;
}

static int NextAttempt(string directory, string key)
{
    var mutexName = "SenpaiFakeG6-" + Convert.ToHexString(
        SHA256.HashData(Encoding.UTF8.GetBytes(directory))).Substring(0, 24);
    using var mutex = new Mutex(false, mutexName);
    mutex.WaitOne();
    try
    {
        var path = Path.Combine(directory, "state.txt");
        var state = ReadMap(path);
        var next = state.TryGetValue(key, out var value)
            ? int.Parse(value, CultureInfo.InvariantCulture) + 1
            : 1;
        state[key] = next.ToString(CultureInfo.InvariantCulture);
        File.WriteAllLines(path, state.OrderBy(item => item.Key).Select(
            item => item.Key + "=" + item.Value));
        return next;
    }
    finally
    {
        mutex.ReleaseMutex();
    }
}

var directory = AppContext.BaseDirectory;
var position = "";
while (Console.ReadLine() is string command)
{
    if (command == "uci")
    {
        Console.WriteLine("id name Senpai Fake G6");
        Console.WriteLine("id author Codex Tests");
        Console.WriteLine("uciok");
    }
    else if (command == "isready")
    {
        Console.WriteLine("readyok");
    }
    else if (command.StartsWith("position fen ", StringComparison.Ordinal))
    {
        position = command[13..];
    }
    else if (command.StartsWith("go nodes ", StringComparison.Ordinal))
    {
        var nodes = int.Parse(command[9..], CultureInfo.InvariantCulture);
        var fields = position.Split(' ', StringSplitOptions.RemoveEmptyEntries);
        var child = fields[^1];
        var behavior = ReadMap(Path.Combine(directory, "behavior.txt"));
        var mode = behavior.TryGetValue(child, out var selected) ? selected : "success";
        var attempt = NextAttempt(directory, child + ":" + nodes);
        if (mode == "retry-crash-shallow" && nodes == 2000 && attempt == 1)
            Environment.Exit(17);
        if (mode == "crash") Environment.Exit(19);
        if (mode == "timeout") Thread.Sleep(3000);
        var scores = new Dictionary<string, int> {
            ["1"] = 30, ["2"] = -10, ["3"] = -10, ["4"] = 100
        };
        var score = scores.TryGetValue(child, out var cp) ? cp : 0;
        if (mode == "malformed")
            Console.WriteLine($"info depth 4 score cp nope nodes {nodes}");
        else if (mode == "mate")
            Console.WriteLine($"info depth 4 score mate 2 nodes {nodes}");
        else if (mode == "bound")
            Console.WriteLine($"info depth 4 score cp {score} lowerbound nodes {nodes}");
        else if (mode == "underrun")
            Console.WriteLine($"info depth 4 score cp {score} nodes {nodes - 1}");
        else if (mode == "stale-score")
            Console.WriteLine($"info depth 4 score cp {score} nodes 100");
        else if (mode == "skipped-depth")
            Console.WriteLine($"info depth 3 score cp {score} nodes {nodes - 1}");
        else
            Console.WriteLine($"info depth 4 score cp {score} nodes {nodes} pv a0a1");
        if (mode == "spam")
            for (var index = 0; index < 100; ++index)
                Console.WriteLine($"info string spam-{index}");
        if (mode == "stale-score")
            Console.WriteLine($"info nodes {nodes}");
        else if (mode == "underrun")
            Console.WriteLine($"info depth 5 nodes {nodes - 1}");
        else if (mode != "missing-final-unscored")
            Console.WriteLine($"info depth 5 nodes {nodes}");
        if (mode == "stderr") Console.Error.WriteLine("unexpected engine stderr");
        Console.WriteLine("bestmove a0a1");
    }
    else if (command == "quit")
    {
        return;
    }
}
"""


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(teacher._canonical_json(value))


class Fixture:
    def __init__(self, directory: Path, engine_bundle: Path, *, publish_claim: bool = True):
        self.root = directory
        self.engine_dir = directory / "engine"
        shutil.copytree(engine_bundle, self.engine_dir)
        self.engine = self.engine_dir / "SenpaiFakeG6.exe"
        self.behavior = self.engine_dir / "behavior.txt"
        self.behavior.write_text("", encoding="utf-8")

        self.routing = directory / "routing.jsonl"
        self.components = directory / "components.jsonl"
        self.prelabel = directory / "prelabel.json"
        self.hce_completion = directory / "hce-completion.json"
        self.options = directory / "teacher-options.json"
        self.claim = directory / "teacher-claim.json"
        self.ledger = directory / "attempts.jsonl"
        self.ledger_completion = directory / "attempts-completion.json"
        self.labels = directory / "teacher-labels.jsonl"
        self.teacher_manifest = directory / "teacher-manifest.json"
        self.projected_corpus = directory / "projected-corpus.jsonl"
        self.projection_manifest = directory / "projection-manifest.json"
        self.completion = directory / "teacher-completion.json"
        self.projection_producer = directory / "projection-producer.py"
        self.projection_producer.write_text("# frozen projection producer\n", encoding="utf-8")

        board = "5k4/10/10/10/10/4P5/10/10/10/4K5[-/-/-/-]"
        children = [
            {
                "childId": f"root-0001-child-{index}",
                "normalizedChildOfen": f"{board} b - - 0 {index}",
            }
            for index in range(1, 5)
        ]
        self.route_rows = [
            {
                "schemaVersion": 1,
                "kind": teacher.ROUTING_KIND,
                "profileId": teacher.PROFILE_ID,
                "rootId": "root-0001",
                "sourceRootId": "source-root-0001",
                "sourceGroupId": "source-group-0001",
                "phase": "endgame",
                "parentSideToMove": "w",
                "children": children,
            }
        ]
        teacher._exclusive_jsonl(self.routing, self.route_rows)
        self.component_rows = [
            {
                "schemaVersion": 1,
                "kind": teacher.COMPONENT_KIND,
                "profileId": teacher.PROFILE_ID,
                "rootId": "root-0001",
                "leakageComponentId": "component-0001",
                "split": "train",
                "sourceRootId": "source-root-0001",
                "sourceGroupId": "source-group-0001",
            }
        ]
        teacher._exclusive_jsonl(self.components, self.component_rows)

        placeholders: dict[str, dict[str, object]] = {}
        for name in (
            "terminal", "forbidden-registry", "forbidden-catalog", "initializer",
            "source-roots", "source-children", "prelabel-producer", "hce-claim",
            "hce-engine", "hce-runner", "hce-options", "hce-transcript",
        ):
            path = directory / f"{name}.artifact"
            path.write_bytes((name + "\n").encode("ascii"))
            placeholders[name] = teacher._identity(path)
        self.placeholders = placeholders
        prelabel = {
            "schemaVersion": 1,
            "kind": teacher.PRELABEL_KIND,
            "profileId": teacher.PROFILE_ID,
            "status": "frozen-before-any-teacher-target-decode",
            "createdUtc": "2026-07-24T00:00:01.000000Z",
            "componentMap": teacher._identity(self.components),
            "targetFreeRouting": teacher._identity(self.routing),
            "terminalClassifierLineage": placeholders["terminal"],
            "priorForbiddenRegistry": placeholders["forbidden-registry"],
            "priorForbiddenCatalogs": [placeholders["forbidden-catalog"]],
            "initializerManifest": placeholders["initializer"],
            "sourceRootManifest": placeholders["source-roots"],
            "sourceChildrenManifest": placeholders["source-children"],
            "producer": placeholders["prelabel-producer"],
            "componentRows": 1,
            "targetFieldsDecodedAtSeal": 0,
            "targetFieldsEmittedAtSeal": 0,
        }
        teacher._exclusive_json(self.prelabel, prelabel)
        roots, _, _ = teacher._parse_routes(self.routing)
        hce = {
            "schemaVersion": 1,
            "kind": teacher.HCE_COMPLETION_KIND,
            "profileId": teacher.PROFILE_ID,
            "status": "completed-before-teacher-and-target-decode",
            "createdUtc": "2026-07-24T00:00:02.000000Z",
            "claim": placeholders["hce-claim"],
            "prelabelSeal": teacher._identity(self.prelabel),
            "targetFreeRouting": teacher._identity(self.routing),
            "engine": placeholders["hce-engine"],
            "runner": placeholders["hce-runner"],
            "options": placeholders["hce-options"],
            "transcript": placeholders["hce-transcript"],
            "inputOrderSha256": teacher._digest(teacher._hce_order(roots)),
            "rows": 4,
            "perspective": teacher.STATIC_HCE_PERSPECTIVE,
            "targetRowsDecodedAtCompletion": 0,
            "targetFieldsDecodedAtCompletion": 0,
            "resultInformationRead": False,
            "finalStageSeal": True,
        }
        teacher._exclusive_json(self.hce_completion, hce)
        teacher.publish_teacher_options(self.options)
        if publish_claim:
            self.publish_claim()

    def publish_claim(self) -> dict[str, object]:
        return teacher.publish_teacher_claim(
            self.claim,
            routing=self.routing,
            components=self.components,
            prelabel=self.prelabel,
            hce_completion=self.hce_completion,
            engine=self.engine,
            options=self.options,
            projection_producer=self.projection_producer,
            projected_corpus=self.projected_corpus,
            projection_manifest=self.projection_manifest,
            created_utc="2026-07-24T00:00:03.000000Z",
        )

    def set_behavior(self, **values: str) -> None:
        self.behavior.write_text(
            "".join(f"{key}={value}\n" for key, value in sorted(values.items())),
            encoding="utf-8",
        )
        state = self.engine_dir / "state.txt"
        if state.exists():
            state.unlink()

    def run(self) -> teacher.LedgerState:
        return teacher.run_teacher(self.claim, self.ledger, resume=False)

    def publish_projection(self) -> None:
        context = teacher._verify_claim(self.claim)
        rows, _ = teacher._parse_teacher_labels(self.labels, context)
        teacher._exclusive_jsonl(
            self.projected_corpus,
            teacher._projected_rows(rows, context.components),
        )
        teacher._exclusive_json(
            self.projection_manifest,
            teacher.projected_manifest_document(
                context=context,
                corpus=self.projected_corpus,
                teacher_labels=self.labels,
                teacher_manifest=self.teacher_manifest,
                created_utc=teacher._utc_after(
                    json.loads(self.teacher_manifest.read_text(encoding="utf-8"))["createdUtc"]
                ),
            ),
        )


class TeacherRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._build = tempfile.TemporaryDirectory()
        root = Path(cls._build.name)
        source = root / "source"
        output = root / "output"
        source.mkdir()
        (source / "SenpaiFakeG6.csproj").write_text(
            textwrap.dedent(FAKE_PROJECT).strip() + "\n", encoding="utf-8"
        )
        (source / "Program.cs").write_text(
            textwrap.dedent(FAKE_SOURCE).strip() + "\n", encoding="utf-8"
        )
        completed = subprocess.run(
            [
                "dotnet", "build", str(source / "SenpaiFakeG6.csproj"),
                "-c", "Release", "-o", str(output), "--nologo",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=120,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stdout + completed.stderr)
        cls.engine_bundle = output

    @classmethod
    def tearDownClass(cls) -> None:
        cls._build.cleanup()

    def fixture(self, directory: str, *, publish_claim: bool = True) -> Fixture:
        return Fixture(Path(directory), self.engine_bundle, publish_claim=publish_claim)

    def test_honest_retry_then_complete_rank_sign_regret_and_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(directory)
            fixture.set_behavior(**{"1": "retry-crash-shallow"})
            state = fixture.run()
            self.assertTrue(state.complete)
            attempts, _ = teacher._load_jsonl(fixture.ledger, "attempt ledger")
            child_one_shallow = [
                row for row in attempts
                if row["childId"].endswith("-1") and row["stage"] == "shallow"
            ]
            self.assertEqual([row["outcome"] for row in child_one_shallow], ["crash", "success"])
            self.assertEqual(len(attempts), 9)
            deep = [row for row in attempts if row["stage"] == "deep"]
            self.assertEqual(len(deep), 4)
            self.assertEqual({row["childId"] for row in deep}, {
                f"root-0001-child-{index}" for index in range(1, 5)
            })
            self.assertTrue(all(row["nodes"] == 50_000 for row in deep))

            teacher.publish_teacher_outputs(
                claim=fixture.claim,
                ledger=fixture.ledger,
                ledger_completion=fixture.ledger_completion,
                labels=fixture.labels,
                teacher_manifest=fixture.teacher_manifest,
            )
            labels, _ = teacher._load_jsonl(fixture.labels, "teacher labels")
            by_child = {row["childId"]: row for row in labels}
            self.assertEqual(by_child["root-0001-child-1"]["deepScoreCpRoot"], -30)
            self.assertEqual(by_child["root-0001-child-2"]["deepScoreCpRoot"], 10)
            self.assertEqual(by_child["root-0001-child-2"]["deepRank"], 1)
            self.assertEqual(by_child["root-0001-child-3"]["deepRank"], 2)
            self.assertEqual(by_child["root-0001-child-3"]["deepRegretCp"], 0)
            self.assertEqual(by_child["root-0001-child-4"]["deepRegretCp"], 110)
            self.assertTrue(
                all(row["deepScoreCpRoot"] == -row["deepScoreCpChildStm"] for row in labels)
            )

            fixture.publish_projection()
            completion = teacher.finalize_teacher(
                claim=fixture.claim,
                ledger=fixture.ledger,
                ledger_completion=fixture.ledger_completion,
                labels=fixture.labels,
                teacher_manifest=fixture.teacher_manifest,
                completion=fixture.completion,
            )
            self.assertTrue(completion["finalStageSeal"])
            self.assertFalse(completion["resultInformationRead"])
            self.assertEqual(completion["budgets"], teacher.BUDGETS)

    def test_resume_continues_a_strict_ledger_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(directory)
            fixture.run()
            rows, _ = teacher._load_jsonl(fixture.ledger, "attempt ledger")
            fixture.ledger.write_bytes(b"".join(teacher._canonical_json(row) for row in rows[:2]))
            state = teacher.run_teacher(fixture.claim, fixture.ledger, resume=True)
            self.assertTrue(state.complete)
            replay, _ = teacher._load_jsonl(fixture.ledger, "attempt ledger")
            self.assertEqual(len(replay), 8)
            self.assertEqual([row["sequence"] for row in replay], list(range(8)))

    def test_timeout_crash_malformed_mate_bound_and_stderr_fail_closed(self) -> None:
        for mode in (
            "timeout", "crash", "malformed", "mate", "bound", "underrun", "stderr"
        ):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                fixture = self.fixture(directory)
                fixture.set_behavior(**{"1": mode})
                if mode == "timeout":
                    original = teacher._Uci.until

                    def quick_until(session, predicate, timeout):
                        return original(session, predicate, 0.12 if timeout == 180 else timeout)

                    patch = mock.patch.object(teacher._Uci, "until", quick_until)
                else:
                    patch = mock.patch.object(teacher._Uci, "until", teacher._Uci.until)
                with patch:
                    with self.assertRaisesRegex(RuntimeError, "exhausted three attempts"):
                        fixture.run()
                rows, _ = teacher._load_jsonl(fixture.ledger, "attempt ledger")
                rejected = [row for row in rows if row["childId"].endswith("-1")]
                expected = {
                    "malformed": "malformed-score",
                    "mate": "mate-score",
                    "bound": "bound-score",
                    "underrun": "node-underrun",
                }.get(mode, mode)
                self.assertEqual([row["outcome"] for row in rejected], [expected] * 3)
                self.assertFalse(fixture.ledger_completion.exists())
                self.assertFalse(fixture.labels.exists())

    def test_senpai_node_cap_score_semantics_reject_stale_missing_and_skipped(self) -> None:
        for mode in ("stale-score", "missing-final-unscored", "skipped-depth"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                fixture = self.fixture(directory)
                fixture.set_behavior(**{"1": mode})
                with self.assertRaisesRegex(RuntimeError, "exhausted three attempts"):
                    fixture.run()
                rows, _ = teacher._load_jsonl(fixture.ledger, "attempt ledger")
                rejected = [row for row in rows if row["childId"].endswith("-1")]
                self.assertEqual(
                    [row["outcome"] for row in rejected],
                    ["malformed-score"] * 3,
                )

    def test_transcript_collection_is_bounded_without_prefix_hash_claims(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(directory)
            fixture.set_behavior(**{"1": "spam"})
            with mock.patch.object(teacher, "MAX_TRANSCRIPT_LINES", 12):
                with self.assertRaisesRegex(RuntimeError, "exhausted three attempts"):
                    fixture.run()
                rows, _ = teacher._load_jsonl(fixture.ledger, "attempt ledger")
                rejected = [row for row in rows if row["childId"].endswith("-1")]
                self.assertEqual(len(rejected), 3)
                self.assertTrue(all(row["outcome"] == "protocol-error" for row in rejected))
                self.assertTrue(all(row["transcriptComplete"] is False for row in rejected))
                self.assertTrue(all(row["stdoutSha256"] is None for row in rejected))
                self.assertTrue(all(row["stderrSha256"] is None for row in rejected))

    def test_wrong_stage_nodes_order_and_duplicate_attempt_are_rejected(self) -> None:
        mutations = {
            "stage": lambda rows: rows[0].__setitem__("stage", "deep"),
            "nodes": lambda rows: rows[0].__setitem__("nodes", 1999),
            "order": lambda rows: rows.__setitem__(slice(0, 2), [rows[1], rows[0]]),
            "duplicate": lambda rows: rows.append(copy.deepcopy(rows[-1])),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                fixture = self.fixture(directory)
                fixture.run()
                rows, _ = teacher._load_jsonl(fixture.ledger, "attempt ledger")
                mutate(rows)
                fixture.ledger.write_bytes(b"".join(teacher._canonical_json(row) for row in rows))
                with self.assertRaises(ValueError):
                    teacher.publish_teacher_outputs(
                        claim=fixture.claim,
                        ledger=fixture.ledger,
                        ledger_completion=fixture.ledger_completion,
                        labels=fixture.labels,
                        teacher_manifest=fixture.teacher_manifest,
                    )

    def test_retained_transcript_hash_and_score_are_freshly_replayed(self) -> None:
        def change_score(rows):
            rows[0]["scoreCpChildStm"] += 1

        def change_transcript_without_hash(rows):
            rows[0]["stdoutLines"] = [
                line.replace("score cp 30", "score cp 31")
                for line in rows[0]["stdoutLines"]
            ]

        def change_transcript_and_hash(rows):
            change_transcript_without_hash(rows)
            rows[0]["stdoutSha256"] = teacher._sha256(
                teacher._transcript_payload(rows[0]["stdoutLines"], "test stdout")
            )

        for name, mutate in {
            "score-only": change_score,
            "transcript-only": change_transcript_without_hash,
            "transcript-and-hash": change_transcript_and_hash,
        }.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                fixture = self.fixture(directory)
                fixture.run()
                rows, _ = teacher._load_jsonl(fixture.ledger, "attempt ledger")
                mutate(rows)
                fixture.ledger.write_bytes(
                    b"".join(teacher._canonical_json(row) for row in rows)
                )
                with self.assertRaises(ValueError):
                    teacher.publish_teacher_outputs(
                        claim=fixture.claim,
                        ledger=fixture.ledger,
                        ledger_completion=fixture.ledger_completion,
                        labels=fixture.labels,
                        teacher_manifest=fixture.teacher_manifest,
                    )

    def test_claimed_engine_mutation_is_rejected_before_search(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(directory)
            fixture.engine.write_bytes(fixture.engine.read_bytes() + b"tamper")
            with self.assertRaisesRegex(ValueError, "identity changed"):
                fixture.run()
            self.assertFalse(fixture.ledger.exists())

    def test_claim_is_no_clobber_and_precedes_target_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(directory, publish_claim=False)
            fixture.projected_corpus.write_text('{"target":1}\n', encoding="utf-8")
            with self.assertRaises(FileExistsError):
                fixture.publish_claim()
            fixture.projected_corpus.unlink()
            fixture.publish_claim()
            with self.assertRaises(FileExistsError):
                fixture.publish_claim()
            self.assertFalse(fixture.labels.exists())
            self.assertFalse(fixture.projection_manifest.exists())
            fixture.run()
            claim = json.loads(fixture.claim.read_text(encoding="utf-8"))
            first = json.loads(fixture.ledger.read_text(encoding="utf-8").splitlines()[0])
            self.assertLess(claim["createdUtc"], first["startedUtc"])

    def test_completion_and_ledger_receipt_are_no_clobber_and_mutation_sensitive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(directory)
            fixture.run()
            teacher.publish_teacher_outputs(
                claim=fixture.claim,
                ledger=fixture.ledger,
                ledger_completion=fixture.ledger_completion,
                labels=fixture.labels,
                teacher_manifest=fixture.teacher_manifest,
            )
            fixture.publish_projection()
            first = teacher.finalize_teacher(
                claim=fixture.claim,
                ledger=fixture.ledger,
                ledger_completion=fixture.ledger_completion,
                labels=fixture.labels,
                teacher_manifest=fixture.teacher_manifest,
                completion=fixture.completion,
            )
            second = teacher.finalize_teacher(
                claim=fixture.claim,
                ledger=fixture.ledger,
                ledger_completion=fixture.ledger_completion,
                labels=fixture.labels,
                teacher_manifest=fixture.teacher_manifest,
                completion=fixture.completion,
            )
            self.assertEqual(first, second)
            fixture.ledger.write_bytes(fixture.ledger.read_bytes() + b"{}\n")
            with self.assertRaises(ValueError):
                teacher.finalize_teacher(
                    claim=fixture.claim,
                    ledger=fixture.ledger,
                    ledger_completion=fixture.ledger_completion,
                    labels=fixture.labels,
                    teacher_manifest=fixture.teacher_manifest,
                    completion=fixture.completion,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
