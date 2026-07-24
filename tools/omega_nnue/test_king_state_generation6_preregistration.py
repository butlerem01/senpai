#!/usr/bin/env python3
"""Hostile tests for the canonical Generation-6 preregistration publisher."""

from __future__ import annotations

from contextlib import ExitStack, redirect_stderr
from datetime import timedelta
import inspect
import io
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

try:
    from . import king_state_generation6_preregistration as host_publisher
    from .test_king_state_train_generation6 import CanonicalFixture, _write_json
except ImportError:  # Trusted sibling imports for direct ``python -I -B`` use.
    _TOOL_DIRECTORY = str(Path(__file__).resolve().parent)
    if _TOOL_DIRECTORY not in sys.path:
        sys.path.insert(0, _TOOL_DIRECTORY)
    import king_state_generation6_preregistration as host_publisher
    from test_king_state_train_generation6 import CanonicalFixture, _write_json


class PublisherFixture:
    """Adapt the complete tiny G6 fixture to the production publisher paths."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.authority = CanonicalFixture(root)
        registry = self.authority.g6.verify_canonical_namespace()
        self.receipt = (
            root
            / "build-msvc/data-generation/omega-decision-v3/80-capsule/"
            "decision-v3.verification.json"
        )
        _write_json(self.receipt, dict(registry.upstream_verification))
        (self.authority.namespace / "00-preregistration.json").unlink()

        tool_directory = root / "tools/omega_nnue"
        self.publisher = tool_directory / host_publisher.__file__.split(os.sep)[-1]
        shutil.copyfile(Path(host_publisher.__file__), self.publisher)
        for name in (
            "omega_decision_v3_trainer_runner.py",
            "omega_decision_v3_evaluator_runner.py",
        ):
            shutil.copyfile(self.authority.runner, tool_directory / name)
        validation_directory = root / "validation"
        shutil.copyfile(
            host_publisher.G5_EARLY_TERMINAL_PROTOCOL,
            validation_directory
            / host_publisher.G5_EARLY_TERMINAL_PROTOCOL.name,
        )
        shutil.copyfile(
            host_publisher.G5_EARLY_TERMINAL_TOOL,
            tool_directory / host_publisher.G5_EARLY_TERMINAL_TOOL.name,
        )
        shutil.copyfile(
            host_publisher.G5_COLOR_COMPAT_READINESS,
            tool_directory / host_publisher.G5_COLOR_COMPAT_READINESS.name,
        )
        shutil.copyfile(
            host_publisher.G5_COLOR_COMPAT_PROTOCOL,
            validation_directory / host_publisher.G5_COLOR_COMPAT_PROTOCOL.name,
        )
        shutil.copyfile(
            host_publisher.G5_COLOR_COMPAT_TEMPLATE,
            validation_directory / host_publisher.G5_COLOR_COMPAT_TEMPLATE.name,
        )

        self.runtime = (
            root / "validation/omega-nnue-king-state-v6-python-runtime.json"
        )
        self.options = (
            root / "validation/omega-nnue-king-state-v6-evaluator-options.json"
        )
        self.preregistration = (
            root / "build-msvc/king-state-v6/00-preregistration.json"
        )

    def run(
        self, *arguments: str, isolated: bool = True
    ) -> subprocess.CompletedProcess[str]:
        command = [sys.executable]
        if isolated:
            command.extend(("-I", "-B"))
        command.extend((str(self.publisher), *arguments))
        return subprocess.run(
            command,
            cwd=self.root,
            env={},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=120,
            check=False,
        )

    def owned_identities(self) -> tuple[dict, dict, dict]:
        identity = self.authority.g6._identity
        return (
            identity(self.runtime),
            identity(self.options),
            identity(self.preregistration),
        )


class Generation6PreregistrationPublisherTests(unittest.TestCase):
    def test_formal_api_and_paths_are_not_caller_selectable(self) -> None:
        for name in (
            "publish_canonical_preregistration",
            "verify_canonical_preregistration",
            "readiness_document",
        ):
            self.assertEqual(
                tuple(inspect.signature(getattr(host_publisher, name)).parameters),
                (),
            )
        self.assertEqual(
            host_publisher.CANONICAL_PREREGISTRATION,
            host_publisher.REPO
            / "build-msvc"
            / "king-state-v6"
            / "00-preregistration.json",
        )
        parser = host_publisher._parser()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(("publish", "--output", "alternate.json"))

    def test_readiness_covers_every_g5_lifecycle_source_and_link_safety(self) -> None:
        lifecycle_names = {
            "g5EarlyTerminalProtocol",
            "g5EarlyTerminalTool",
            "g5ColorCompatReadiness",
            "g5ColorCompatProtocol",
            "g5ColorCompatTemplate",
        }
        with tempfile.TemporaryDirectory(
            prefix="omega-g6-prereg-readiness-"
        ) as directory:
            fixture = PublisherFixture(Path(directory))
            completed = fixture.run("readiness")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            readiness = json.loads(completed.stdout)
            self.assertTrue(lifecycle_names <= set(readiness["requiredPaths"]))
            self.assertTrue(all(readiness["present"][name] for name in lifecycle_names))

            protocol = Path(readiness["requiredPaths"]["g5ColorCompatProtocol"])
            alias = protocol.with_name("compat-protocol-hardlink-readiness.json")
            try:
                os.link(protocol, alias)
            except OSError as error:
                self.skipTest(f"hardlinks unavailable: {error}")
            linked = fixture.run("readiness")
            self.assertEqual(linked.returncode, 0, linked.stderr)
            linked_readiness = json.loads(linked.stdout)
            self.assertEqual(linked_readiness["status"], "awaiting-prerequisites")
            self.assertFalse(linked_readiness["present"]["g5ColorCompatProtocol"])

        with tempfile.TemporaryDirectory(
            prefix="omega-g6-prereg-readiness-byte-drift-"
        ) as directory:
            fixture = PublisherFixture(Path(directory))
            protocol = (
                fixture.root
                / "validation/omega-nnue-king-state-v5-color-compat-protocol.json"
            )
            protocol.write_bytes(protocol.read_bytes() + b"\n")
            changed = fixture.run("readiness")
            self.assertEqual(changed.returncode, 0, changed.stderr)
            changed_readiness = json.loads(changed.stdout)
            self.assertEqual(changed_readiness["status"], "awaiting-prerequisites")
            self.assertFalse(
                changed_readiness["present"]["g5ColorCompatProtocol"]
            )

    def test_publish_is_exact_chronological_and_no_clobber(self) -> None:
        with tempfile.TemporaryDirectory(prefix="omega-g6-prereg-") as directory:
            fixture = PublisherFixture(Path(directory))
            completed = fixture.run("publish")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            publication = json.loads(completed.stdout)
            self.assertEqual(publication["status"], "published-and-freshly-verified")
            self.assertFalse(publication["resultInformationRead"])
            self.assertEqual(publication["targetRowsDecodedByPublisher"], 0)
            self.assertEqual(publication["targetFieldsDecodedByPublisher"], 0)
            self.assertFalse(publication["gameResultsRead"])

            g6 = fixture.authority.g6
            preregistration = json.loads(
                fixture.preregistration.read_text(encoding="utf-8")
            )
            capsule = json.loads(
                fixture.authority.paths["capsule"].read_text(encoding="utf-8")
            )
            expected_created = (
                g6._parse_timestamp(capsule["createdUtc"], "capsule")
                + timedelta(microseconds=1)
            ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            self.assertEqual(preregistration["createdUtc"], expected_created)
            self.assertEqual(
                Path(preregistration["runtimeManifest"]["path"]), fixture.runtime
            )
            self.assertEqual(
                Path(preregistration["evaluatorOptions"]["path"]), fixture.options
            )
            self.assertEqual(
                Path(preregistration["trainerRunner"]["path"]),
                fixture.root / "tools/omega_nnue/omega_decision_v3_trainer_runner.py",
            )
            self.assertEqual(
                Path(preregistration["evaluatorRunner"]["path"]),
                fixture.root / "tools/omega_nnue/omega_decision_v3_evaluator_runner.py",
            )
            self.assertEqual(
                json.loads(fixture.runtime.read_text(encoding="utf-8")),
                g6.runtime_manifest_document(),
            )
            self.assertEqual(
                json.loads(fixture.options.read_text(encoding="utf-8")),
                g6.evaluator_options_document(),
            )
            registry = g6.verify_canonical_namespace()
            receipt = json.loads(fixture.receipt.read_text(encoding="utf-8"))
            self.assertEqual(receipt, registry.upstream_verification)

            verified = fixture.run("verify")
            self.assertEqual(verified.returncode, 0, verified.stderr)
            before = fixture.owned_identities()
            refused = fixture.run("publish")
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("refusing to replace", refused.stderr)
            self.assertEqual(fixture.owned_identities(), before)

    def test_changed_receipt_fails_before_any_publication(self) -> None:
        with tempfile.TemporaryDirectory(prefix="omega-g6-prereg-") as directory:
            fixture = PublisherFixture(Path(directory))
            receipt = json.loads(fixture.receipt.read_text(encoding="utf-8"))
            receipt["resultInformationRead"] = True
            _write_json(fixture.receipt, receipt)
            completed = fixture.run("publish")
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn(
                "verification receipt differs from fresh replay", completed.stderr
            )
            self.assertFalse(fixture.runtime.exists())
            self.assertFalse(fixture.options.exists())
            self.assertFalse(fixture.preregistration.exists())

    def test_publish_requires_isolated_no_bytecode_runtime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="omega-g6-prereg-") as directory:
            fixture = PublisherFixture(Path(directory))
            completed = fixture.run("publish", isolated=False)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("requires pinned Python -I -B", completed.stderr)
            self.assertFalse(fixture.runtime.exists())
            self.assertFalse(fixture.options.exists())
            self.assertFalse(fixture.preregistration.exists())

    def test_entire_g5_lifecycle_authority_is_exact_pinned(self) -> None:
        for relative_path in (
            "validation/omega-nnue-king-state-v5-early-terminal-protocol.json",
            "tools/omega_nnue/king_state_generation5_early_terminal.py",
            "tools/omega_nnue/king_state_match_readiness_generation5_compat_v2.py",
            "validation/omega-nnue-king-state-v5-color-compat-protocol.json",
            "validation/omega-nnue-king-state-v5-color-compat-preregistration.template.json",
        ):
            with self.subTest(relative_path=relative_path), tempfile.TemporaryDirectory(
                prefix="omega-g6-prereg-early-pin-"
            ) as directory:
                fixture = PublisherFixture(Path(directory))
                changed = fixture.root / relative_path
                changed.write_bytes(changed.read_bytes() + b"\n")
                completed = fixture.run("publish")
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(
                    "differs from its Generation-6 exact pin", completed.stderr
                )
                self.assertFalse(fixture.runtime.exists())
                self.assertFalse(fixture.options.exists())
                self.assertFalse(fixture.preregistration.exists())

    def test_lifecycle_source_hardlink_is_rejected_before_publication(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="omega-g6-prereg-lifecycle-link-"
        ) as directory:
            fixture = PublisherFixture(Path(directory))
            protocol = (
                fixture.root
                / "validation/omega-nnue-king-state-v5-color-compat-protocol.json"
            )
            alias = protocol.with_name("compat-protocol-hardlink-alias.json")
            try:
                os.link(protocol, alias)
            except OSError as error:
                self.skipTest(f"hardlinks unavailable: {error}")
            completed = fixture.run("publish")
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("regular unlinked file", completed.stderr)
            self.assertFalse(fixture.runtime.exists())
            self.assertFalse(fixture.options.exists())
            self.assertFalse(fixture.preregistration.exists())

    def test_publisher_rejects_hardlinked_cli_entry(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="omega-g6-prereg-entry-link-"
        ) as directory:
            fixture = PublisherFixture(Path(directory))
            alias = fixture.publisher.with_name("preregistration-hardlink.py")
            try:
                os.link(fixture.publisher, alias)
            except OSError as error:
                self.skipTest(f"hardlinks unavailable: {error}")
            completed = subprocess.run(
                [sys.executable, "-I", "-B", str(alias), "readiness"],
                cwd=fixture.root,
                env={},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=60,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("regular unlinked file", completed.stderr)

    def test_return_boundary_rejects_same_payload_on_replaced_inode(self) -> None:
        """A validator/result hook cannot exchange an owned canonical file."""

        with tempfile.TemporaryDirectory(prefix="omega-g6-prereg-race-") as directory:
            root = Path(directory).resolve()
            namespace = root / "build-msvc/king-state-v6"
            validation = root / "validation"
            namespace.mkdir(parents=True)
            validation.mkdir(parents=True)
            runtime = validation / "runtime.json"
            options = validation / "options.json"
            preregistration = namespace / "00-preregistration.json"
            receipt = root / "receipt.json"
            receipt.write_bytes(b"receipt\n")

            preregistration_document = {"test": "preregistration"}
            preregistration_payload = host_publisher.training._canonical_json(
                preregistration_document
            )
            runtime_payload = b'{"test":"runtime"}\n'
            evaluator_payload = b'{"test":"evaluator"}\n'
            receipt_identity = host_publisher.training._identity(receipt)
            original_result = host_publisher._publication_result

            def replace_after_result(**kwargs):
                result = original_result(**kwargs)
                preregistration.replace(namespace / "displaced-owned-preregistration")
                preregistration.write_bytes(preregistration_payload)
                return result

            patches = (
                mock.patch.object(
                    host_publisher, "CANONICAL_NAMESPACE", namespace
                ),
                mock.patch.object(
                    host_publisher, "CANONICAL_RUNTIME_MANIFEST", runtime
                ),
                mock.patch.object(
                    host_publisher, "CANONICAL_EVALUATOR_OPTIONS", options
                ),
                mock.patch.object(
                    host_publisher, "CANONICAL_PREREGISTRATION", preregistration
                ),
                mock.patch.object(
                    host_publisher, "CANONICAL_VERIFICATION_RECEIPT", receipt
                ),
                mock.patch.object(
                    host_publisher,
                    "_OWNED_OUTPUTS",
                    (runtime, options, preregistration),
                ),
                mock.patch.object(host_publisher, "_require_isolated_runtime"),
                mock.patch.object(
                    host_publisher,
                    "_candidate_documents",
                    return_value=(
                        preregistration_document,
                        runtime_payload,
                        evaluator_payload,
                        receipt_identity,
                        {},
                    ),
                ),
                mock.patch.object(host_publisher, "_prepare_namespace"),
                mock.patch.object(
                    host_publisher.training,
                    "verify_canonical_namespace",
                    return_value=types.SimpleNamespace(document={}),
                ),
                mock.patch.object(host_publisher, "_verify_publisher_paths"),
                mock.patch.object(
                    host_publisher,
                    "_publication_result",
                    side_effect=replace_after_result,
                ),
            )
            with ExitStack() as stack:
                for patch in patches:
                    stack.enter_context(patch)
                with self.assertRaisesRegex(
                    RuntimeError, "rollback also failed"
                ):
                    host_publisher.publish_canonical_preregistration()

            self.assertFalse(runtime.exists())
            self.assertFalse(options.exists())
            # The replacement is not the inode owned by this invocation and
            # therefore must never be removed by rollback.
            self.assertTrue(preregistration.exists())

    def test_descriptor_owned_inode_survives_post_helper_same_byte_swap(self) -> None:
        """Each returned owner tuple names the descriptor, not a replacement."""

        for attacked_field in (
            "runtimeManifest",
            "evaluatorOptions",
            "preregistration",
        ):
            with self.subTest(attacked_field=attacked_field), tempfile.TemporaryDirectory(
                prefix="omega-g6-prereg-owned-race-"
            ) as directory:
                root = Path(directory).resolve()
                namespace = root / "build-msvc/king-state-v6"
                validation = root / "validation"
                namespace.mkdir(parents=True)
                validation.mkdir(parents=True)
                paths = {
                    "runtimeManifest": validation / "runtime.json",
                    "evaluatorOptions": validation / "options.json",
                    "preregistration": namespace / "00-preregistration.json",
                }
                receipt = root / "receipt.json"
                receipt.write_bytes(b"receipt\n")
                preregistration_document = {"test": "preregistration"}
                payloads = {
                    "runtimeManifest": b'{"test":"runtime"}\n',
                    "evaluatorOptions": b'{"test":"evaluator"}\n',
                    "preregistration": host_publisher.training._canonical_json(
                        preregistration_document
                    ),
                }
                receipt_identity = host_publisher.training._identity(receipt)
                original_exclusive = (
                    host_publisher.training._exclusive_bytes_owned
                )
                displaced = root / f"displaced-{attacked_field}"
                attacked_path = paths[attacked_field]

                def replace_after_helper(path, payload):
                    result = original_exclusive(path, payload)
                    if path == attacked_path:
                        path.replace(displaced)
                        path.write_bytes(payload)
                    return result

                patches = (
                    mock.patch.object(
                        host_publisher, "CANONICAL_NAMESPACE", namespace
                    ),
                    mock.patch.object(
                        host_publisher,
                        "CANONICAL_RUNTIME_MANIFEST",
                        paths["runtimeManifest"],
                    ),
                    mock.patch.object(
                        host_publisher,
                        "CANONICAL_EVALUATOR_OPTIONS",
                        paths["evaluatorOptions"],
                    ),
                    mock.patch.object(
                        host_publisher,
                        "CANONICAL_PREREGISTRATION",
                        paths["preregistration"],
                    ),
                    mock.patch.object(
                        host_publisher, "CANONICAL_VERIFICATION_RECEIPT", receipt
                    ),
                    mock.patch.object(
                        host_publisher,
                        "_OWNED_OUTPUTS",
                        tuple(paths.values()),
                    ),
                    mock.patch.object(host_publisher, "_require_isolated_runtime"),
                    mock.patch.object(
                        host_publisher,
                        "_candidate_documents",
                        return_value=(
                            preregistration_document,
                            payloads["runtimeManifest"],
                            payloads["evaluatorOptions"],
                            receipt_identity,
                            {},
                        ),
                    ),
                    mock.patch.object(host_publisher, "_prepare_namespace"),
                    mock.patch.object(
                        host_publisher.training,
                        "verify_canonical_namespace",
                        return_value=types.SimpleNamespace(document={}),
                    ),
                    mock.patch.object(host_publisher, "_verify_publisher_paths"),
                    mock.patch.object(
                        host_publisher.training,
                        "_exclusive_bytes_owned",
                        side_effect=replace_after_helper,
                    ),
                )
                with ExitStack() as stack:
                    for patch in patches:
                        stack.enter_context(patch)
                    with self.assertRaisesRegex(
                        RuntimeError, "rollback also failed"
                    ):
                        host_publisher.publish_canonical_preregistration()

                self.assertTrue(displaced.exists())
                self.assertTrue(attacked_path.exists())
                self.assertEqual(
                    attacked_path.read_bytes(), displaced.read_bytes()
                )
                for field, path in paths.items():
                    if field != attacked_field:
                        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
