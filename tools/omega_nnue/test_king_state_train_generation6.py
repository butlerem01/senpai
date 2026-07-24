#!/usr/bin/env python3
"""Hostile tests for the single-lineage Generation-6 decision foundation."""

from __future__ import annotations

import copy
import importlib.util
import inspect
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

try:
    from . import king_state_train_generation6 as host_g6
    from . import validate_king_state_v6_training_protocol as protocol_validator
except ImportError:  # Trusted absolute sibling import for direct -I execution.
    module_directory = str(Path(__file__).resolve().parent)
    if module_directory not in sys.path:
        sys.path.insert(0, module_directory)
    import king_state_train_generation6 as host_g6
    import validate_king_state_v6_training_protocol as protocol_validator


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, sort_keys=True, allow_nan=False, separators=(",", ":"))
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


RUNNER_SOURCE = r'''#!/usr/bin/env python3
import hashlib
import json
import os
from pathlib import Path
import sys

def file_identity(path):
    resolved = Path(path).resolve()
    payload = resolved.read_bytes()
    return {
        "path": str(resolved),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }

mode = sys.argv[1]
if mode == "--verify-omega-decision-v3-capsule":
    capsule_path, options_path, initializer_path = sys.argv[2:]
    capsule = json.loads(Path(capsule_path).read_text(encoding="utf-8"))
    verifier_options = json.loads(Path(options_path).read_text(encoding="utf-8"))
    initializer = json.loads(Path(initializer_path).read_text(encoding="utf-8"))
    routing = [
        json.loads(line)
        for line in Path(capsule["targetFreeRouting"]["path"]).read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    components = [
        json.loads(line)
        for line in Path(capsule["componentMap"]["path"]).read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    attempts = [
        json.loads(line)
        for line in Path(capsule["teacherAttemptLedger"]["path"]).read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    terminal = json.loads(
        Path(capsule["terminalClassifierLineage"]["path"]).read_text(
            encoding="utf-8"
        )
    )
    hce_completion = json.loads(
        Path(capsule["preTargetHceCompletion"]["path"]).read_text(
            encoding="utf-8"
        )
    )
    catalog_positions = 0
    exact_overlaps = 0
    signature_overlaps = 0
    source_overlaps = 0
    for identity in capsule["priorForbiddenCatalogs"]:
        manifest = json.loads(Path(identity["path"]).read_text(encoding="utf-8"))
        catalog_positions += int(manifest["positionCount"])
        exact_overlaps += int(manifest["exactPositionOverlaps"])
        signature_overlaps += int(manifest["conservativeSignatureOverlaps"])
        source_overlaps += int(manifest["sourceArtifactOverlaps"])
    routed_children = sum(len(row["children"]) for row in routing)
    promoted = initializer["selectionMode"] == "promoted-prior"
    result = {
        "schemaVersion": 1,
        "kind": "omega-decision-v3-fresh-verification",
        "profileId": "king-state-v6-move-decision-v1",
        "status": "passed-fresh-semantic-replay",
        "capsule": file_identity(capsule_path),
        "verifierExecutable": capsule["upstreamVerifierExecutable"],
        "verifierRunner": capsule["upstreamVerifierRunner"],
        "verifierOptions": capsule["upstreamVerifierOptions"],
        "initializerAuthority": {
            "manifest": capsule["initializerManifest"],
            "selectionMode": initializer["selectionMode"],
            "selectedCatalogIndex": initializer["selectedCatalogIndex"],
            "selectedModel": initializer["model"],
            "catalogSourceIds": [row["sourceId"] for row in initializer["orderedCatalog"]],
            "firstEligibleSelected": True,
            "selectionSemanticsVerified": True,
            "selectedPromotionHealthPassed": True if promoted else None,
            "sourceClosureSemanticsVerified": True,
            "fallbackProtocolReplayed": not promoted,
            "g6TargetRowsDecoded": 0,
            "resultInformationRead": False,
        },
        "terminalAuthority": {
            "lineage": capsule["terminalClassifierLineage"],
            "routedChildren": routed_children,
            "terminalChildrenExcludedBeforeRouting": terminal["terminalChildrenExcludedBeforeRouting"],
            "unclassifiedChildren": terminal["unclassifiedChildren"],
            "errorTextAcceptedAsTerminal": terminal["errorTextAcceptedAsTerminal"],
            "rulesSemanticsReplayed": terminal["status"] == "complete-rules-semantic-classification",
            "completionSemanticsVerified": terminal["finalStageSeal"] is True,
        },
        "priorForbiddenAuthority": {
            "registry": capsule["priorForbiddenRegistry"],
            "requiredSourceIds": verifier_options["requiredPriorForbiddenSourceIds"],
            "catalogs": capsule["priorForbiddenCatalogs"],
            "catalogPositions": catalog_positions,
            "manifestsSemanticallyReplayed": True,
            "exactPositionOverlaps": exact_overlaps,
            "conservativeSignatureOverlaps": signature_overlaps,
            "sourceArtifactOverlaps": source_overlaps,
        },
        "componentAuthority": {
            "componentMap": capsule["componentMap"],
            "roots": len(components),
            "components": len({row["leakageComponentId"] for row in components}),
            "wholeComponentSplits": True,
            "semanticsReplayed": True,
        },
        "staticHceAuthority": {
            "claim": capsule["preTargetHceClaim"],
            "completion": capsule["preTargetHceCompletion"],
            "teacherClaim": capsule["teacherClaim"],
            "prelabelSeal": capsule["prelabelSeal"],
            "targetFreeRouting": capsule["targetFreeRouting"],
            "engine": capsule["staticHceEngine"],
            "runner": capsule["staticHceRunner"],
            "options": capsule["staticHceOptions"],
            "transcript": capsule["staticHceTranscript"],
            "inputOrderSha256": hce_completion["inputOrderSha256"],
            "rows": hce_completion["rows"],
            "perspective": hce_completion["perspective"],
            "freshReplayMatches": True,
            "completedBeforeTeacherClaim": True,
            "semanticsReplayed": True,
        },
        "teacherLedgerAuthority": {
            "claim": capsule["teacherClaim"],
            "attemptLedger": capsule["teacherAttemptLedger"],
            "attemptLedgerCompletion": capsule["teacherAttemptLedgerCompletion"],
            "completion": capsule["teacherCompletion"],
            "budgets": capsule["teacherBudgets"],
            "routedChildren": routed_children,
            "attemptRecords": len(attempts),
            "successfulChildren": len({row["childId"] for row in attempts if row["status"] == "success"}),
            "rejectedChildren": len({row["childId"] for row in attempts if row["status"] != "success"}),
            "unresolvedChildren": 0,
            "semanticsReplayed": True,
        },
        "projectionAuthority": {
            "plannedProducer": capsule["plannedProjectionProducer"],
            "plannedCorpusPath": capsule["plannedProjectedCorpusPath"],
            "plannedManifestPath": capsule["plannedProjectionManifestPath"],
            "actualProducer": capsule["projectionProducer"],
            "actualCorpus": capsule["projectedCorpus"],
            "actualManifest": capsule["labelManifest"],
            "producerMatches": capsule["plannedProjectionProducer"] == capsule["projectionProducer"],
            "pathsMatch": capsule["plannedProjectedCorpusPath"] == capsule["projectedCorpus"]["path"] and capsule["plannedProjectionManifestPath"] == capsule["labelManifest"]["path"],
            "semanticsReplayed": True,
        },
        "resultInformationRead": False,
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
elif mode in ("--evaluate-handcrafted-stream", "--evaluate-network-stream"):
    for line in sys.stdin:
        if line.strip():
            print(0)
elif mode == "--validate-network-health":
    model = sys.argv[2]
    payload = open(model, "rb").read()
    print(json.dumps({
        "modelSha256": hashlib.sha256(payload).hexdigest(),
        "modelBytes": len(payload),
        "finitePredictions": True,
        "quantizationRoundTripExact": True,
        "expectedNetworkBytes": True,
        "runtimeParity": True,
        "maximumAbsResidualCp": 0,
    }, sort_keys=True, separators=(",", ":")))
elif mode == "--train-generation6":
    import numpy as np
    (model_id, candidate, purpose, seed_text, recipe_text, optimizer_text,
     train_corpus, train_hce, initializer, output_batch_order, output_model,
     output_history) = sys.argv[2:]
    recipe = json.loads(recipe_text)
    optimizer = json.loads(optimizer_text)
    rows_by_root = {}
    with open(train_corpus, "r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                row = json.loads(line)
                rows_by_root.setdefault(row["rootId"], []).append(row)
    for root_rows in rows_by_root.values():
        root_rows.sort(key=lambda row: row["childId"])
    hce_by_child = {}
    with open(train_hce, "r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                row = json.loads(line)
                hce_by_child[row["childId"]] = row
    roots = sorted(rows_by_root)
    root_count = len(roots)
    batches = root_count // optimizer["rootsPerBatch"]
    consumed_children = 0
    model_chain = b""
    with open(output_batch_order, "x", encoding="utf-8", newline="\n") as transcript:
        for epoch in range(1, optimizer["epochs"] + 1):
            digest = hashlib.sha256(
                (seed_text + "|epoch|" + str(epoch)).encode("utf-8")
            ).digest()
            epoch_seed = int.from_bytes(digest[:8], "little", signed=False)
            permutation = np.random.default_rng(epoch_seed).permutation(root_count)
            ordered = [roots[int(index)] for index in permutation]
            for batch_index, start in enumerate(
                range(0, root_count, optimizer["rootsPerBatch"]), 1
            ):
                batch_roots = ordered[start:start + optimizer["rootsPerBatch"]]
                batch_labels = [
                    row for root_id in batch_roots for row in rows_by_root[root_id]
                ]
                batch_children = [row["childId"] for row in batch_labels]
                consumed_children += len(batch_children)
                label_payload = "".join(
                    json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
                    for row in batch_labels
                ).encode("utf-8")
                hce_payload = "".join(
                    json.dumps(hce_by_child[child_id], sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
                    for child_id in batch_children
                ).encode("utf-8")
                actual_row = {
                    "schemaVersion": 1,
                    "kind": "omega-nnue-king-state-v6-actual-batch-order",
                    "profileId": "king-state-v6-move-decision-v1",
                    "modelId": model_id,
                    "candidateId": candidate,
                    "purpose": purpose,
                    "seed": int(seed_text),
                    "recipe": recipe,
                    "epoch": epoch,
                    "batchIndex": batch_index,
                    "optimizerStep": (epoch - 1) * batches + batch_index,
                    "qatEnabled": epoch >= optimizer["firstQatEpoch"],
                    "rootIds": batch_roots,
                    "childIds": batch_children,
                    "batchContentSha256": hashlib.sha256(
                        label_payload + hce_payload
                    ).hexdigest(),
                }
                actual_line = json.dumps(
                    actual_row, sort_keys=True, separators=(",", ":"), allow_nan=False
                ) + "\n"
                # The transcript row is appended only after this synthetic
                # optimizer step has consumed the complete ordered batch.
                model_chain = hashlib.sha256(
                    model_chain + actual_line.encode("utf-8")
                ).digest()
                transcript.write(actual_line)
                transcript.flush()
                os.fsync(transcript.fileno())
    batch_payload = open(output_batch_order, "rb").read()
    actual_batch_sha256 = hashlib.sha256(batch_payload).hexdigest()
    if consumed_children != root_count * 4 * optimizer["epochs"]:
        raise SystemExit("trainer did not consume every complete root batch")
    with open(output_model, "xb") as stream:
        stream.write(("G6MODEL:" + model_id + ":" + model_chain.hex() + "\n").encode("ascii"))
    epochs = []
    for epoch in range(1, optimizer["epochs"] + 1):
        qat = epoch >= optimizer["firstQatEpoch"]
        rate = optimizer["learningRate"] * (optimizer["qatLearningRateScale"] if qat else 1.0)
        epochs.append({
            "epoch": epoch,
            "qatEnabled": qat,
            "learningRate": rate,
            "optimizerSteps": epoch * batches,
            "rootsSeen": root_count,
            "completeRootBatches": batches,
            "validationRootsDecoded": 0,
            "heldOutRootsDecoded": 0,
            "heldOutTargetFieldsDecoded": 0,
        })
    history = {
        "schemaVersion": 1,
        "kind": "omega-nnue-king-state-v6-training-history",
        "profileId": "king-state-v6-move-decision-v1",
        "modelId": model_id,
        "candidateId": candidate,
        "purpose": purpose,
        "seed": int(seed_text),
        "recipe": recipe,
        "optimizerProtocol": optimizer,
        "batchOrderSha256": actual_batch_sha256,
        "actualBatchOrderTranscript": {
            "path": os.path.abspath(output_batch_order),
            "bytes": len(batch_payload),
            "sha256": actual_batch_sha256,
        },
        "epochs": epochs,
        "resultInformationRead": False,
        "validationRootsDecoded": 0,
        "heldOutRootsDecoded": 0,
        "heldOutTargetFieldsDecoded": 0,
    }
    with open(output_history, "x", encoding="utf-8", newline="\n") as stream:
        json.dump(history, stream, sort_keys=True, separators=(",", ":"), allow_nan=False)
        stream.write("\n")
else:
    raise SystemExit("unknown mode")
'''


class CanonicalFixture:
    """Build a complete tiny authority under an unchanged copied module."""

    def __init__(
        self,
        root: Path,
        *,
        initializer_mode: str = "promoted-prior",
        omit_last_teacher_attempt: bool = False,
        planned_projection_to_teacher: bool = False,
        sequential_trainer: bool = False,
        terminal_unclassified: bool = False,
        boolean_zero_verifier: bool = False,
        boolean_initializer_index: bool = False,
        non_integer_hce_rows: bool = False,
        forbidden_overlap: bool = False,
        force_second_promoted_initializer: bool = False,
        first_prior_initializer_ineligible: bool = False,
        prelabel_terminal_substitution: bool = False,
        prelabel_forbidden_substitution: bool = False,
        prelabel_initializer_substitution: bool = False,
        teacher_claim_wrong_hce_completion: bool = False,
        hce_completion_equal_teacher_claim: bool = False,
        teacher_manifest_before_claim: bool = False,
        teacher_completion_before_label_manifest: bool = False,
        hce_manifest_after_capsule: bool = False,
        preregistration_before_capsule: bool = False,
    ) -> None:
        self.root = root
        module_path = root / "tools/omega_nnue/king_state_train_generation6.py"
        module_path.parent.mkdir(parents=True)
        shutil.copyfile(Path(host_g6.__file__), module_path)
        spec = importlib.util.spec_from_file_location(
            f"isolated_g6_{id(self)}", module_path
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        self.g6 = module
        self.namespace = root / "build-msvc/king-state-v6"
        self.namespace.mkdir(parents=True)
        self.data = root / "upstream"
        self.data.mkdir()
        self.runner = (
            root / "tools/omega_nnue/verify_omega_decision_v3_upstream.py"
        )
        runner_source = RUNNER_SOURCE
        if sequential_trainer:
            runner_source = runner_source.replace(
                "permutation = np.random.default_rng(epoch_seed).permutation(root_count)",
                "permutation = np.arange(root_count)",
            )
        if boolean_zero_verifier:
            runner_source = runner_source.replace(
                '"unclassifiedChildren": terminal["unclassifiedChildren"],',
                '"unclassifiedChildren": False,',
            )
        if boolean_initializer_index:
            runner_source = runner_source.replace(
                '"selectedCatalogIndex": initializer["selectedCatalogIndex"],',
                '"selectedCatalogIndex": True,',
            )
        if non_integer_hce_rows:
            runner_source = runner_source.replace(
                '"rows": hce_completion["rows"],',
                '"rows": float(hce_completion["rows"]),',
            )
        self.runner.write_text(runner_source, encoding="utf-8")
        self.initializer_mode = initializer_mode
        self.omit_last_teacher_attempt = omit_last_teacher_attempt
        self.planned_projection_to_teacher = planned_projection_to_teacher
        self.terminal_unclassified = terminal_unclassified
        self.forbidden_overlap = forbidden_overlap
        self.force_second_promoted_initializer = force_second_promoted_initializer
        self.first_prior_initializer_ineligible = first_prior_initializer_ineligible
        self.prelabel_terminal_substitution = prelabel_terminal_substitution
        self.prelabel_forbidden_substitution = prelabel_forbidden_substitution
        self.prelabel_initializer_substitution = prelabel_initializer_substitution
        self.teacher_claim_wrong_hce_completion = teacher_claim_wrong_hce_completion
        self.hce_completion_equal_teacher_claim = hce_completion_equal_teacher_claim
        self.teacher_manifest_before_claim = teacher_manifest_before_claim
        self.teacher_completion_before_label_manifest = (
            teacher_completion_before_label_manifest
        )
        self.hce_manifest_after_capsule = hce_manifest_after_capsule
        self.preregistration_before_capsule = preregistration_before_capsule
        self._build_authority()

    def _identity(self, path: Path) -> dict:
        return self.g6._identity(path)

    def _build_initializer_authority(self) -> tuple[Path, Path, Path, Path]:
        g6 = self.g6
        initializer = self.data / "I0.nnue"
        if self.initializer_mode == "promoted-prior":
            initializer.write_bytes(b"PROMOTED PRIOR INITIALIZER\n")
        elif self.initializer_mode == "deterministic-fallback":
            initializer.write_bytes(b"DETERMINISTIC FALLBACK INITIALIZER\n")
        else:
            raise ValueError(self.initializer_mode)
        initializer_producer = self.data / "initializer-producer.bin"
        initializer_producer.write_bytes(b"initializer producer\n")
        initializer_selection = self.data / "initializer-selection.json"
        g5_selection = self.data / "g5-selection.json"
        g5_closure = self.data / "g5-closure.json"
        g2_selection = self.data / "g2-k2-selection.json"
        g2_closure = self.data / "g2-k2-closure.json"
        promoted = self.initializer_mode == "promoted-prior"
        g5_promoted = promoted and not self.first_prior_initializer_ineligible
        _write_json(g5_selection, {
            "kind": "generation5-selection-seal",
            "status": "selected-validation-winner" if g5_promoted else "closed-no-eligible-candidate",
            "selectedModel": self._identity(initializer) if g5_promoted else None,
            "healthPassed": True if g5_promoted else None,
        })
        _write_json(g5_closure, {
            "kind": "generation5-terminal-closure",
            "outcome": "promoted" if g5_promoted else "closed-no-eligible-candidate",
            "selection": self._identity(g5_selection),
            "winnerModel": self._identity(initializer) if g5_promoted else None,
            "winnerHealthPassed": True if g5_promoted else None,
        })
        _write_json(g2_selection, {
            "kind": "generation2-k2-selection-seal",
            "status": "selected-validation-winner" if promoted else "failed",
            "selectedModel": self._identity(initializer) if promoted else None,
            "healthPassed": True if promoted else None,
        })
        _write_json(g2_closure, {
            "kind": "generation2-k2-terminal-closure",
            "outcome": "promoted" if promoted else "aborted",
            "selection": self._identity(g2_selection),
            "winnerModel": self._identity(initializer) if promoted else None,
            "winnerHealthPassed": True if promoted else None,
        })
        ordered_catalog = [
            {
                "sourceId": "G5",
                "selectionSeal": self._identity(g5_selection),
                "closure": self._identity(g5_closure),
                "model": self._identity(initializer) if g5_promoted else None,
                "promotionStatus": "promoted" if g5_promoted else "failed",
            },
            {
                "sourceId": "G2-K2",
                "selectionSeal": self._identity(g2_selection),
                "closure": self._identity(g2_closure),
                "model": self._identity(initializer) if promoted else None,
                "promotionStatus": "promoted" if promoted else "aborted",
            },
        ]
        selected_catalog_index = (
            1
            if promoted
            and (
                self.force_second_promoted_initializer
                or self.first_prior_initializer_ineligible
            )
            else (0 if promoted else None)
        )
        source_closure = (
            g2_closure if selected_catalog_index == 1 else g5_closure
        )
        _write_json(initializer_selection, {
            "schemaVersion": 1,
            "kind": "omega-decision-v3-pre-g6-initializer-selection",
            "profileId": g6.PROFILE_ID,
            "selectionMode": self.initializer_mode,
            "selectedCatalogIndex": selected_catalog_index,
            "selectedModel": self._identity(initializer),
            "orderedCatalog": ordered_catalog,
            "fallbackProtocol": dict(g6.INITIALIZER_FALLBACK_PROTOCOL),
            "g6TargetRowsDecoded": 0,
            "resultInformationRead": False,
        })
        initializer_manifest = self.data / "initializer.manifest.json"
        _write_json(initializer_manifest, {
            "schemaVersion": 1,
            "kind": g6.INITIALIZER_MANIFEST_KIND,
            "profileId": g6.PROFILE_ID,
            "status": "frozen-pre-g6-initializer-selection",
            "createdUtc": "2026-07-24T00:00:00.000001Z",
            "resultInformationRead": False,
            "model": self._identity(initializer),
            "producer": self._identity(initializer_producer),
            "selectionMode": self.initializer_mode,
            "selectedCatalogIndex": selected_catalog_index,
            "selectionSeal": self._identity(initializer_selection),
            "sourceClosure": self._identity(source_closure),
            "orderedCatalog": ordered_catalog,
            "fallbackProtocol": dict(g6.INITIALIZER_FALLBACK_PROTOCOL),
        })
        return initializer, initializer_selection, source_closure, initializer_manifest

    def _build_authority(self) -> None:
        g6 = self.g6
        corpus = self.data / "projected-labels.jsonl"
        label_manifest = self.data / "labels.manifest.json"
        teacher_manifest = self.data / "teacher.manifest.json"
        components_path = self.data / "component-map.jsonl"
        routing_path = self.data / "target-free-routing.jsonl"
        teacher_labels_path = self.data / "teacher-labels.jsonl"
        source_root_manifest = self.data / "source-roots.manifest.json"
        source_child_manifest = self.data / "source-children.manifest.json"
        prelabel_producer = self.data / "prelabel-producer.bin"
        teacher_producer = self.data / "teacher-producer.bin"
        projection_producer = self.data / "projection-producer.bin"
        forbidden_registry_producer = self.data / "forbidden-registry-producer.bin"
        terminal_classifier = self.data / "terminal-classifier.lineage.json"
        forbidden_catalog = self.data / "prior-forbidden.jsonl"
        forbidden_manifest = self.data / "prior-forbidden.manifest.json"
        teacher_options = self.data / "teacher-options.json"
        teacher_ledger = self.data / "teacher-attempts.jsonl"
        for path, payload in (
            (prelabel_producer, b"prelabel producer\n"),
            (teacher_producer, b"teacher producer\n"),
            (projection_producer, b"projection producer\n"),
            (forbidden_registry_producer, b"forbidden registry producer\n"),
        ):
            path.write_bytes(payload)
        _write_json(source_root_manifest, {"kind": "source-root-inventory", "roots": 80})
        _write_json(source_child_manifest, {"kind": "source-child-inventory", "children": 320})
        _write_json(teacher_options, {"variant": "omega", "threads": 1})

        projected: list[dict] = []
        teacher: list[dict] = []
        components: list[dict] = []
        routes: list[dict] = []
        ordinal = 0
        regrets = (0, 25, 26, 100)
        for split in g6.SPLITS:
            roots_per_cell = 8 if split == "train" else 1
            for phase in g6.PHASES:
                for side in g6.SIDES:
                    for repetition in range(roots_per_cell):
                        root_id = f"{split}-{phase}-{side}-{repetition}"
                        source_root = f"source-{ordinal}"
                        source_group = f"group-{ordinal}"
                        component = f"component-{ordinal}"
                        child_side = "b" if side == "w" else "w"
                        children = []
                        components.append({
                            "schemaVersion": 1,
                            "kind": g6.COMPONENT_ROW_KIND,
                            "profileId": g6.PROFILE_ID,
                            "rootId": root_id,
                            "leakageComponentId": component,
                            "split": split,
                            "sourceRootId": source_root,
                            "sourceGroupId": source_group,
                        })
                        for child in range(4):
                            child_id = f"{root_id}-c{child}"
                            ofen = (
                                "10/10/10/10/10/10/10/10/10/10[-/-/-/-] "
                                f"{child_side} - - {child} {ordinal + 1}"
                            )
                            children.append({
                                "childId": child_id,
                                "normalizedChildOfen": ofen,
                            })
                            score = 200 - regrets[child]
                            teacher_row = {
                                "schemaVersion": 1,
                                "kind": g6.UPSTREAM_TEACHER_LABEL_KIND,
                                "profileId": g6.PROFILE_ID,
                                "rootId": root_id,
                                "childId": child_id,
                                "childOfen": ofen,
                                "phase": phase,
                                "parentSideToMove": side,
                                "deepRank": child + 1,
                                "deepRegretCp": regrets[child],
                                "deepScoreCpRoot": score,
                                "deepScoreCpChildStm": -score,
                            }
                            teacher.append(teacher_row)
                            projected.append({
                                "schemaVersion": 1,
                                "kind": g6.LABEL_KIND,
                                "rootId": root_id,
                                "leakageComponentId": component,
                                "split": split,
                                "childId": child_id,
                                "childOfen": ofen,
                                "phase": phase,
                                "parentSideToMove": side,
                                "deepRank": child + 1,
                                "deepRegretCp": regrets[child],
                                "deepScoreCpRoot": score,
                                "deepScoreCpChildStm": -score,
                            })
                        routes.append({
                            "schemaVersion": 1,
                            "kind": g6.UPSTREAM_ROUTING_KIND,
                            "profileId": g6.PROFILE_ID,
                            "rootId": root_id,
                            "sourceRootId": source_root,
                            "sourceGroupId": source_group,
                            "phase": phase,
                            "parentSideToMove": side,
                            "children": children,
                        })
                        ordinal += 1
        _write_jsonl(components_path, components)
        _write_jsonl(routing_path, routes)
        _write_json(terminal_classifier, {
            "schemaVersion": 1,
            "kind": "omega-decision-v3-terminal-classifier-lineage",
            "profileId": g6.PROFILE_ID,
            "status": "complete-rules-semantic-classification",
            "routedChildren": len(projected),
            "terminalChildrenExcludedBeforeRouting": 0,
            "unclassifiedChildren": 1 if self.terminal_unclassified else 0,
            "errorTextAcceptedAsTerminal": False,
            "resultInformationRead": False,
            "finalStageSeal": True,
        })
        _write_jsonl(forbidden_catalog, [{
            "schemaVersion": 1,
            "kind": "omega-target-opaque-forbidden-position",
            "exactPositionKey": "0" * 64,
        }])
        _write_json(forbidden_manifest, {
            "schemaVersion": 1,
            "kind": "omega-target-opaque-forbidden-position-catalog-manifest",
            "status": "semantically-verified-prior-catalog",
            "catalog": self._identity(forbidden_catalog),
            "positionCount": 1,
            "exactPositionOverlaps": 1 if self.forbidden_overlap else 0,
            "conservativeSignatureOverlaps": 0,
            "sourceArtifactOverlaps": 0,
            "resultInformationRead": False,
        })
        attempt_rows = [
            {
                "schemaVersion": 1,
                "kind": "omega-decision-v3-teacher-attempt",
                "rootId": row["rootId"],
                "childId": row["childId"],
                "attempt": 1,
                "status": "success",
            }
            for row in teacher
        ]
        if self.omit_last_teacher_attempt:
            attempt_rows.pop()
        _write_jsonl(teacher_ledger, attempt_rows)

        (
            initializer,
            initializer_selection,
            source_closure,
            initializer_manifest,
        ) = self._build_initializer_authority()
        prelabel_initializer = initializer_manifest
        if self.prelabel_initializer_substitution:
            prelabel_initializer = self.data / "substituted-initializer.manifest.json"
            shutil.copyfile(initializer_manifest, prelabel_initializer)
        prelabel_terminal = terminal_classifier
        if self.prelabel_terminal_substitution:
            prelabel_terminal = self.data / "substituted-terminal-classifier.json"
            shutil.copyfile(terminal_classifier, prelabel_terminal)
        forbidden_registry = self.data / "prior-forbidden.registry.json"
        g6.publish_upstream_forbidden_registry(
            forbidden_registry,
            catalog_groups=((g6.UPSTREAM_REQUIRED_PRIOR_SOURCE_IDS, forbidden_manifest),),
            producer=forbidden_registry_producer,
            created_utc="2026-07-24T00:00:00.000001Z",
        )
        prelabel_forbidden = forbidden_manifest
        prelabel_forbidden_registry = forbidden_registry
        if self.prelabel_forbidden_substitution:
            prelabel_forbidden = self.data / "substituted-forbidden.manifest.json"
            shutil.copyfile(forbidden_manifest, prelabel_forbidden)
            prelabel_forbidden_registry = (
                self.data / "substituted-prior-forbidden.registry.json"
            )
            g6.publish_upstream_forbidden_registry(
                prelabel_forbidden_registry,
                catalog_groups=(
                    (g6.UPSTREAM_REQUIRED_PRIOR_SOURCE_IDS, prelabel_forbidden),
                ),
                producer=forbidden_registry_producer,
                created_utc="2026-07-24T00:00:00.000001Z",
            )
        prelabel_path = self.data / "prelabel.json"
        g6.publish_upstream_prelabel_seal(
            prelabel_path,
            component_map=components_path,
            target_free_routing=routing_path,
            terminal_classifier_lineage=prelabel_terminal,
            prior_forbidden_registry=prelabel_forbidden_registry,
            prior_forbidden_catalogs=(prelabel_forbidden,),
            initializer_manifest=prelabel_initializer,
            source_root_manifest=source_root_manifest,
            source_children_manifest=source_child_manifest,
            producer=prelabel_producer,
            created_utc="2026-07-24T00:00:00.000002Z",
        )

        hce_options = self.data / "hce-options.json"
        hce_projection = self.data / "hce.jsonl"
        _write_json(hce_options, g6.static_hce_options_document())
        hce_claim = self.data / "hce.claim.json"
        _write_json(hce_claim, {
            "schemaVersion": 1,
            "kind": g6.UPSTREAM_HCE_CLAIM_KIND,
            "profileId": g6.PROFILE_ID,
            "status": "claimed-before-teacher-and-target-decode",
            "createdUtc": "2026-07-24T00:00:00.000003Z",
            "prelabelSeal": self._identity(prelabel_path),
            "targetFreeRouting": self._identity(routing_path),
            "engine": self._identity(Path(sys.executable)),
            "runner": self._identity(self.runner),
            "options": self._identity(hce_options),
            "plannedTranscriptPath": str(hce_projection.resolve()),
            "targetRowsDecodedAtClaim": 0,
            "targetFieldsDecodedAtClaim": 0,
            "resultInformationRead": False,
        })
        _write_jsonl(hce_projection, [
            {
                "schemaVersion": 1,
                "kind": g6.HCE_ROW_KIND,
                "profileId": g6.PROFILE_ID,
                "childId": row["childId"],
                "handcraftedCpChildStm": 0,
            }
            for row in sorted(projected, key=lambda item: item["childId"])
        ])
        hce_completion = self.data / "hce.completion.json"
        g6.publish_upstream_hce_completion(
            hce_completion,
            claim=hce_claim,
            prelabel_seal=prelabel_path,
            target_free_routing=routing_path,
            engine=Path(sys.executable),
            runner=self.runner,
            options=hce_options,
            transcript=hce_projection,
            created_utc="2026-07-24T00:00:00.000004Z",
        )
        claimed_hce_completion = hce_completion
        if self.teacher_claim_wrong_hce_completion:
            claimed_hce_completion = self.data / "wrong-hce-completion.json"
            _write_json(claimed_hce_completion, {"wrong": True})

        route_digest = g6._sha256_bytes(g6._canonical_json(
            [g6._parse_target_free_routing(routing_path)[key]
             for key in sorted(g6._parse_target_free_routing(routing_path))]
        ))
        planned_corpus = (
            teacher_labels_path if self.planned_projection_to_teacher else corpus
        )
        planned_manifest = (
            teacher_manifest if self.planned_projection_to_teacher else label_manifest
        )
        teacher_claim = self.data / "teacher.claim.json"
        _write_json(teacher_claim, {
            "schemaVersion": 1,
            "kind": g6.UPSTREAM_TEACHER_CLAIM_KIND,
            "profileId": g6.PROFILE_ID,
            "status": "claimed-before-first-teacher-target-decode",
            "createdUtc": (
                "2026-07-24T00:00:00.000004Z"
                if self.hce_completion_equal_teacher_claim
                else "2026-07-24T00:00:00.000005Z"
            ),
            "prelabelSeal": self._identity(prelabel_path),
            "targetFreeRouting": self._identity(routing_path),
            "componentMap": self._identity(components_path),
            "engine": self._identity(Path(sys.executable)),
            "runner": self._identity(self.runner),
            "options": self._identity(teacher_options),
            "budgets": dict(g6.UPSTREAM_TEACHER_BUDGETS),
            "inputOrderSha256": route_digest,
            "plannedProjectionProducer": self._identity(projection_producer),
            "plannedProjectedCorpusPath": str(planned_corpus.resolve()),
            "plannedProjectionManifestPath": str(planned_manifest.resolve()),
            "preTargetHceCompletion": self._identity(claimed_hce_completion),
            "targetRowsDecodedAtClaim": 0,
            "targetFieldsDecodedAtClaim": 0,
            "resultInformationRead": False,
        })
        _write_jsonl(teacher_labels_path, teacher)
        g6.publish_upstream_teacher_manifest(
            teacher_manifest,
            prelabel_seal=prelabel_path,
            component_map=components_path,
            labels=teacher_labels_path,
            producer=teacher_producer,
            created_utc=(
                "2026-07-24T00:00:00.000004Z"
                if self.teacher_manifest_before_claim
                else "2026-07-24T00:00:00.000006Z"
            ),
        )
        _write_jsonl(corpus, projected)
        g6.publish_label_manifest(
            label_manifest,
            corpus=corpus,
            component_map=components_path,
            upstream_prelabel_seal=prelabel_path,
            upstream_teacher_labels=teacher_labels_path,
            upstream_teacher_manifest=teacher_manifest,
            projection_producer=projection_producer,
            created_utc="2026-07-24T00:00:00.000007Z",
        )
        hce_manifest = self.data / "hce.manifest.json"
        g6.publish_static_hce_manifest(
            hce_manifest,
            corpus=corpus,
            label_manifest=label_manifest,
            component_map=components_path,
            projection=hce_projection,
            engine=Path(sys.executable),
            runner=self.runner,
            options=hce_options,
            created_utc=(
                "2026-07-24T00:00:00.000011Z"
                if self.hce_manifest_after_capsule
                else "2026-07-24T00:00:00.000008Z"
            ),
        )
        teacher_ledger_completion = self.data / "teacher-attempts.complete.json"
        _write_json(teacher_ledger_completion, {
            "schemaVersion": 1,
            "kind": "omega-decision-v3-teacher-attempt-ledger-completion",
            "status": "complete-exact-child-coverage",
            "ledger": self._identity(teacher_ledger),
            "successfulChildren": len(teacher),
            "rejectedChildren": 0,
            "maximumAttempts": g6.UPSTREAM_TEACHER_BUDGETS["maximumAttempts"],
            "finalStageSeal": True,
            "resultInformationRead": False,
        })
        teacher_completion = self.data / "teacher.completion.json"
        _write_json(teacher_completion, {
            "schemaVersion": 1,
            "kind": g6.UPSTREAM_TEACHER_COMPLETION_KIND,
            "profileId": g6.PROFILE_ID,
            "status": "completed-exact-claimed-teacher-and-projection",
            "createdUtc": (
                "2026-07-24T00:00:00.000006Z"
                if self.teacher_completion_before_label_manifest
                else "2026-07-24T00:00:00.000009Z"
            ),
            "claim": self._identity(teacher_claim),
            "prelabelSeal": self._identity(prelabel_path),
            "engine": self._identity(Path(sys.executable)),
            "runner": self._identity(self.runner),
            "options": self._identity(teacher_options),
            "budgets": dict(g6.UPSTREAM_TEACHER_BUDGETS),
            "inputOrderSha256": route_digest,
            "attemptLedger": self._identity(teacher_ledger),
            "attemptLedgerCompletion": self._identity(teacher_ledger_completion),
            "teacherLabels": self._identity(teacher_labels_path),
            "teacherManifest": self._identity(teacher_manifest),
            "projectionProducer": self._identity(projection_producer),
            "projectedCorpus": self._identity(corpus),
            "labelManifest": self._identity(label_manifest),
            "finalStageSeal": True,
            "resultInformationRead": False,
        })

        evaluator_options = self.data / "evaluator-options.json"
        _write_json(evaluator_options, g6.evaluator_options_document())
        runtime_manifest = self.data / "runtime-manifest.json"
        _write_json(runtime_manifest, g6.runtime_manifest_document())
        upstream_verifier_options = self.data / "upstream-verifier-options.json"
        _write_json(
            upstream_verifier_options, g6.upstream_verifier_options_document()
        )

        authority = g6.verify_training_authority(
            corpus=corpus,
            authority=g6.TrainingAuthority(
                label_manifest=label_manifest,
                component_map=components_path,
                hce_projection=hce_projection,
                hce_manifest=hce_manifest,
                hce_engine=Path(sys.executable),
                hce_runner=self.runner,
                hce_options=hce_options,
            ),
        )
        capsule_path = (
            self.root
            / "build-msvc/data-generation/omega-decision-v3/capsule.closure.json"
        )
        _write_json(capsule_path, {
            "schemaVersion": 1,
            "kind": g6.UPSTREAM_CAPSULE_KIND,
            "profileId": g6.PROFILE_ID,
            "status": "closed-pretarget-to-final-projection-lineage",
            "createdUtc": "2026-07-24T00:00:00.000010Z",
            "upstreamVerifierExecutable": self._identity(Path(sys.executable)),
            "upstreamVerifierRunner": self._identity(self.runner),
            "upstreamVerifierOptions": self._identity(upstream_verifier_options),
            "targetFreeRouting": self._identity(routing_path),
            "componentMap": self._identity(components_path),
            "prelabelSeal": self._identity(prelabel_path),
            "terminalClassifierLineage": self._identity(terminal_classifier),
            "initializerSelection": self._identity(initializer_selection),
            "initializerClosure": self._identity(source_closure),
            "initializerModel": self._identity(initializer),
            "initializerManifest": self._identity(initializer_manifest),
            "plannedProjectionProducer": self._identity(projection_producer),
            "plannedProjectedCorpusPath": str(planned_corpus.resolve()),
            "plannedProjectionManifestPath": str(planned_manifest.resolve()),
            "priorForbiddenRegistry": self._identity(forbidden_registry),
            "priorForbiddenCatalogs": [self._identity(forbidden_manifest)],
            "teacherClaim": self._identity(teacher_claim),
            "teacherEngine": self._identity(Path(sys.executable)),
            "teacherRunner": self._identity(self.runner),
            "teacherOptions": self._identity(teacher_options),
            "teacherBudgets": dict(g6.UPSTREAM_TEACHER_BUDGETS),
            "teacherInputOrderSha256": route_digest,
            "teacherAttemptLedger": self._identity(teacher_ledger),
            "teacherAttemptLedgerCompletion": self._identity(teacher_ledger_completion),
            "teacherCompletion": self._identity(teacher_completion),
            "projectionProducer": self._identity(projection_producer),
            "teacherLabels": self._identity(teacher_labels_path),
            "teacherManifest": self._identity(teacher_manifest),
            "projectedCorpus": self._identity(corpus),
            "labelManifest": self._identity(label_manifest),
            "preTargetHceClaim": self._identity(hce_claim),
            "preTargetHceCompletion": self._identity(hce_completion),
            "staticHceEngine": self._identity(Path(sys.executable)),
            "staticHceRunner": self._identity(self.runner),
            "staticHceOptions": self._identity(hce_options),
            "staticHceTranscript": self._identity(hce_projection),
            "staticHceManifest": self._identity(hce_manifest),
            "rootInventories": authority.binding["rootInventories"],
            "phaseSideInventories": authority.binding["phaseSideInventories"],
            "closureDeclaration": dict(g6.UPSTREAM_CAPSULE_DECLARATION),
            "resultInformationRead": False,
            "finalStageSeal": True,
        })

        protocol = self.root / "validation/omega-nnue-king-state-v6-training-protocol.json"
        _write_json(protocol, g6.protocol_document())
        prereg = {
            "schemaVersion": 1,
            "kind": g6.PREREGISTRATION_KIND,
            "profileId": g6.PROFILE_ID,
            "status": "frozen-single-lineage-before-generation6-training",
            "createdUtc": (
                "2026-07-24T00:00:00.000009Z"
                if self.preregistration_before_capsule
                else "2026-07-24T00:00:00.000011Z"
            ),
            "namespace": str(self.namespace.resolve()),
            "artifactPaths": dict(g6.CANONICAL_ARTIFACT_PATHS),
            "protocol": self._identity(protocol),
            "contractSource": self._identity(Path(g6.__file__)),
            "upstreamCapsule": self._identity(capsule_path),
            "upstreamVerifierExecutable": self._identity(Path(sys.executable)),
            "upstreamVerifierRunner": self._identity(self.runner),
            "upstreamVerifierOptions": self._identity(upstream_verifier_options),
            "corpus": self._identity(corpus),
            "labelManifest": self._identity(label_manifest),
            "componentMap": self._identity(components_path),
            "staticHceProjection": self._identity(hce_projection),
            "staticHceManifest": self._identity(hce_manifest),
            "staticHceEngine": self._identity(Path(sys.executable)),
            "staticHceRunner": self._identity(self.runner),
            "staticHceOptions": self._identity(hce_options),
            "trainerExecutable": self._identity(Path(sys.executable)),
            "trainerRunner": self._identity(self.runner),
            "runtimeManifest": self._identity(runtime_manifest),
            "trainerCommandProtocol": dict(g6.TRAINER_COMMAND_PROTOCOL),
            "evaluatorExecutable": self._identity(Path(sys.executable)),
            "evaluatorRunner": self._identity(self.runner),
            "evaluatorOptions": self._identity(evaluator_options),
            "initializerModel": self._identity(initializer),
            "initializerManifest": self._identity(initializer_manifest),
            "optimizerProtocol": dict(g6.OPTIMIZER_PROTOCOL),
            "candidateRecipes": {
                candidate: dict(g6.CANDIDATE_RECIPES[candidate])
                for candidate in g6.CANDIDATES
            },
            "primaryTrainingSeeds": {
                candidate: g6._domain_seed(
                    g6.PRIMARY_TRAINING_SEED_BASE, "primary-training", candidate
                )
                for candidate in g6.CANDIDATES
            },
            "robustnessTrainingSeeds": {
                candidate: g6._domain_seed(
                    g6.ROBUSTNESS_TRAINING_SEED_BASE,
                    "robustness-training",
                    candidate,
                )
                for candidate in g6.CANDIDATES
            },
            "authoritySha256": authority.binding_sha256,
            "rootInventories": authority.binding["rootInventories"],
            "phaseSideInventories": authority.binding["phaseSideInventories"],
            "resultInformationRead": False,
            "heldOutTargetRowsDecodedAtFreeze": 0,
            "heldOutTargetFieldsDecodedAtFreeze": 0,
        }
        _write_json(self.namespace / "00-preregistration.json", prereg)
        self.paths = {
            "corpus": corpus,
            "component": components_path,
            "routing": routing_path,
            "capsule": capsule_path,
            "hce": hce_projection,
            "forbidden_registry": forbidden_registry,
        }

    def run_to_selection(self) -> str:
        g6 = self.g6
        g6.materialize_canonical_authority()
        for candidate in g6.CANDIDATES:
            g6.train_primary(candidate)
        for model_id in ("I0", *g6.CANDIDATES):
            g6.evaluate_validation(model_id)
        g6.select_primary()
        selection = g6._verify_primary_selection(g6.verify_canonical_namespace())
        return selection["selectedCandidateId"]

    def run_to_arm(self) -> str:
        selected = self.run_to_selection()
        self.g6.train_robustness_selected()
        self.g6.evaluate_robustness_selected()
        self.g6.seal_robustness()
        self.g6.arm_heldout()
        return selected


class Generation6Tests(unittest.TestCase):
    def test_protocol_and_objective_are_exact(self) -> None:
        report = protocol_validator.validate_protocol()
        self.assertEqual(report["status"], "passed")
        self.assertEqual(host_g6.TIE_PRIORITY, ("G6A", "G6B", "G6C"))
        root = host_g6._synthetic_root("gradient")
        predictions = {
            label.sibling_id: label.residual_target_cp + index * 7.0
            for index, label in enumerate(root.siblings)
        }
        objective = host_g6.decision_root_objective(root, predictions, "G6B")
        epsilon = 1e-4
        for sibling in root.sibling_ids:
            plus, minus = dict(predictions), dict(predictions)
            plus[sibling] += epsilon
            minus[sibling] -= epsilon
            numerical = (
                host_g6.decision_root_objective(root, plus, "G6B").total_loss
                - host_g6.decision_root_objective(root, minus, "G6B").total_loss
            ) / (2 * epsilon)
            self.assertTrue(math.isclose(
                numerical,
                objective.gradient_by_sibling[sibling],
                rel_tol=2e-5,
                abs_tol=2e-7,
            ))

    def test_formal_api_has_no_caller_evidence_inputs(self) -> None:
        forbidden = {"path", "model", "roots", "predictions", "report", "health"}
        for name in (
            "materialize_canonical_authority",
            "select_primary",
            "train_robustness_selected",
            "evaluate_robustness_selected",
            "seal_robustness",
            "arm_heldout",
            "consume_heldout_once",
        ):
            parameters = set(inspect.signature(getattr(host_g6, name)).parameters)
            self.assertFalse(parameters & forbidden, (name, parameters))
        self.assertFalse(hasattr(host_g6.LabelAccess, "heldout_evaluation"))
        with self.assertRaises(RuntimeError):
            host_g6.publish_metric_report({}, {})

    def test_end_to_end_is_single_lineage_and_aggregate_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(Path(directory))
            selected = fixture.run_to_arm()
            self.assertEqual(selected, "G6A")
            report_identity = fixture.g6.consume_heldout_once()
            report = json.loads(Path(report_identity["path"]).read_text(encoding="utf-8"))
            self.assertEqual(set(report["modelMetrics"]), {"I0", "G6A"})
            self.assertNotIn("roots", report)
            serialized = json.dumps(report, sort_keys=True)
            self.assertNotIn("childOfen", serialized)
            self.assertFalse(report["predictionsEmitted"])
            with self.assertRaises(fixture.g6.HeldoutAccessError):
                fixture.g6.consume_heldout_once()
            registry = fixture.g6.verify_canonical_namespace()
            report_path = fixture.g6._canonical_slot(registry, "heldoutReport")
            report_bytes = report_path.read_bytes()
            substituted_report = json.loads(report_bytes.decode("utf-8"))
            substituted_report["schemaVersion"] = True
            _write_json(report_path, substituted_report)
            with self.assertRaisesRegex(
                ValueError,
                "held-out report header/leakage declaration changed",
            ):
                fixture.g6._verify_heldout_closure(registry)
            report_path.write_bytes(report_bytes)
            fixture.g6._verify_heldout_closure(registry)
            future = fixture.g6._canonical_slot(
                registry, "trainingClaim:G6B-robustness"
            )
            _write_json(future, {"fabricated": True})
            with self.assertRaises(ValueError):
                fixture.g6._verify_heldout_closure(registry)

    def test_capsule_routing_substitution_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(Path(directory))
            rows = [json.loads(line) for line in fixture.paths["routing"].read_text().splitlines()]
            rows[0]["children"][0]["normalizedChildOfen"] = (
                "10/10/10/10/10/10/10/10/10/10[-/-/-/-] b - - 9 999"
            )
            _write_jsonl(fixture.paths["routing"], rows)
            with self.assertRaises(ValueError):
                fixture.g6.verify_canonical_namespace()

    def test_deterministic_initializer_fallback_is_authorized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(
                Path(directory), initializer_mode="deterministic-fallback"
            )
            registry = fixture.g6.verify_canonical_namespace()
            self.assertEqual(
                registry.upstream_verification["initializerAuthority"][
                    "selectionMode"
                ],
                "deterministic-fallback",
            )
            self.assertTrue(
                registry.upstream_verification["initializerAuthority"][
                    "fallbackProtocolReplayed"
                ]
            )

    def test_incomplete_teacher_ledger_is_rejected_by_fresh_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(
                Path(directory), omit_last_teacher_attempt=True
            )
            with self.assertRaises(ValueError):
                fixture.g6.verify_canonical_namespace()

    def test_terminal_classifier_gap_is_rejected_by_fresh_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(
                Path(directory), terminal_unclassified=True
            )
            with self.assertRaises(ValueError):
                fixture.g6.verify_canonical_namespace()

    def test_boolean_zero_verifier_counter_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(
                Path(directory), boolean_zero_verifier=True
            )
            with self.assertRaises(ValueError):
                fixture.g6.verify_canonical_namespace()

    def test_boolean_initializer_index_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(
                Path(directory),
                boolean_initializer_index=True,
                first_prior_initializer_ineligible=True,
            )
            with self.assertRaisesRegex(
                ValueError, "upstream initializer semantic replay changed"
            ):
                fixture.g6.verify_canonical_namespace()

    def test_non_integer_static_hce_row_count_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(Path(directory), non_integer_hce_rows=True)
            with self.assertRaisesRegex(ValueError, "upstream static-HCE replay changed"):
                fixture.g6.verify_canonical_namespace()

    def test_prelabel_binds_terminal_forbidden_and_initializer_authorities(self) -> None:
        for option in (
            "prelabel_terminal_substitution",
            "prelabel_forbidden_substitution",
            "prelabel_initializer_substitution",
        ):
            with self.subTest(option=option), tempfile.TemporaryDirectory() as directory:
                fixture = CanonicalFixture(Path(directory), **{option: True})
                expected_error = (
                    "upstream prelabel seal differs from recomputation"
                )
                with self.assertRaisesRegex(
                    ValueError, expected_error
                ):
                    fixture.g6.verify_canonical_namespace()

    def test_teacher_targets_and_projection_are_created_inside_claim_window(self) -> None:
        cases = (
            (
                "teacher-manifest-before-claim",
                {"teacher_manifest_before_claim": True},
                "upstream teacher target/projection chronology changed",
            ),
            (
                "completion-before-label-manifest",
                {"teacher_completion_before_label_manifest": True},
                "upstream teacher target/projection chronology changed",
            ),
            (
                "static-hce-manifest-after-capsule",
                {"hce_manifest_after_capsule": True},
                "upstream static-HCE/capsule chronology changed",
            ),
            (
                "preregistration-before-capsule",
                {"preregistration_before_capsule": True},
                "preregistered authority/chronology changed",
            ),
        )
        for name, options, expected_error in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                fixture = CanonicalFixture(Path(directory), **options)
                with self.assertRaisesRegex(ValueError, expected_error):
                    fixture.g6.verify_canonical_namespace()

    def test_teacher_claim_binds_exact_hce_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(
                Path(directory), teacher_claim_wrong_hce_completion=True
            )
            with self.assertRaisesRegex(
                ValueError, "capsule teacher claim is not the frozen plan"
            ):
                fixture.g6.verify_canonical_namespace()

    def test_forbidden_registry_cannot_omit_required_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(Path(directory))
            registry_path = fixture.paths["forbidden_registry"]
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            registry["requiredSourceIds"] = ["G3", "G4"]
            registry["catalogs"][0]["coveredSourceIds"] = ["G3", "G4"]
            substituted_registry = registry_path.with_name(
                "incomplete-prior-forbidden.registry.json"
            )
            _write_json(substituted_registry, registry)
            capsule = json.loads(fixture.paths["capsule"].read_text(encoding="utf-8"))
            capsule["priorForbiddenRegistry"] = fixture._identity(substituted_registry)
            _write_json(fixture.paths["capsule"], capsule)
            preregistration = fixture.namespace / "00-preregistration.json"
            prereg = json.loads(preregistration.read_text(encoding="utf-8"))
            prereg["upstreamCapsule"] = fixture._identity(fixture.paths["capsule"])
            _write_json(preregistration, prereg)
            with self.assertRaisesRegex(
                ValueError, "prior-forbidden registry header changed"
            ):
                fixture.g6.verify_canonical_namespace()

    def test_forbidden_registry_rejects_distinct_paths_to_same_inode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "g3.manifest.json"
            second = root / "g4-g5.manifest.json"
            producer = root / "producer.bin"
            _write_json(first, {"source": "G3"})
            _write_json(second, {"source": "G4-G5"})
            producer.write_bytes(b"producer\n")
            identities = {
                first: host_g6._identity(first),
                second: host_g6._identity(second),
            }

            def same_inode(path: Path) -> tuple[dict, tuple[int, int]]:
                return identities[Path(path)], (17, 29)

            with mock.patch.object(
                host_g6, "_identity_with_inode", side_effect=same_inode
            ), self.assertRaisesRegex(
                ValueError, "prior-forbidden registry repeats a catalog manifest"
            ):
                host_g6.expected_upstream_forbidden_registry(
                    catalog_groups=(
                        (("G3",), first),
                        (("G4", "G5"), second),
                    ),
                    producer=producer,
                    created_utc="2026-07-24T00:00:00.000001Z",
                )

    def test_hce_completion_strictly_precedes_teacher_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(
                Path(directory), hce_completion_equal_teacher_claim=True
            )
            with self.assertRaisesRegex(
                ValueError, "upstream pre-target/teacher chronology changed"
            ):
                fixture.g6.verify_canonical_namespace()

    def test_hce_completion_rejects_reordered_or_forged_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(Path(directory))
            capsule = json.loads(fixture.paths["capsule"].read_text(encoding="utf-8"))
            original_rows = [
                json.loads(line)
                for line in fixture.paths["hce"].read_text(encoding="utf-8").splitlines()
            ]
            original_claim = json.loads(
                Path(capsule["preTargetHceClaim"]["path"]).read_text(encoding="utf-8")
            )
            cases = (
                ("reordered", list(reversed(original_rows)), "row order differs"),
                (
                    "forged-score",
                    [
                        {**row, "handcraftedCpChildStm": 1}
                        if index == 0
                        else row
                        for index, row in enumerate(original_rows)
                    ],
                    "differs from fresh engine replay",
                ),
            )
            for name, rows, expected_error in cases:
                with self.subTest(name=name):
                    transcript = fixture.data / f"{name}.hce.jsonl"
                    claim = fixture.data / f"{name}.hce.claim.json"
                    _write_jsonl(transcript, rows)
                    claim_document = dict(original_claim)
                    claim_document["plannedTranscriptPath"] = str(transcript.resolve())
                    _write_json(claim, claim_document)
                    with self.assertRaisesRegex(ValueError, expected_error):
                        fixture.g6.expected_upstream_hce_completion(
                            claim=claim,
                            prelabel_seal=Path(capsule["prelabelSeal"]["path"]),
                            target_free_routing=Path(
                                capsule["targetFreeRouting"]["path"]
                            ),
                            engine=Path(capsule["staticHceEngine"]["path"]),
                            runner=Path(capsule["staticHceRunner"]["path"]),
                            options=Path(capsule["staticHceOptions"]["path"]),
                            transcript=transcript,
                            created_utc="2026-07-24T00:00:00.000004Z",
                        )

    def test_hce_completion_rejects_shared_paths_and_inodes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(Path(directory))
            capsule = json.loads(fixture.paths["capsule"].read_text(encoding="utf-8"))
            prelabel = Path(capsule["prelabelSeal"]["path"])
            common = {
                "target_free_routing": Path(capsule["targetFreeRouting"]["path"]),
                "engine": Path(capsule["staticHceEngine"]["path"]),
                "runner": Path(capsule["staticHceRunner"]["path"]),
                "options": Path(capsule["staticHceOptions"]["path"]),
                "transcript": Path(capsule["staticHceTranscript"]["path"]),
                "created_utc": "2026-07-24T00:00:00.000004Z",
            }
            with self.subTest(alias="same-path"), self.assertRaisesRegex(
                ValueError, "pre-target HCE authority roles share a path or inode"
            ):
                fixture.g6.expected_upstream_hce_completion(
                    claim=prelabel,
                    prelabel_seal=prelabel,
                    **common,
                )

            claim_hardlink = fixture.data / "claim-hardlink-to-prelabel.json"
            os.link(prelabel, claim_hardlink)
            with self.subTest(alias="same-inode"), self.assertRaisesRegex(
                ValueError, "pre-target HCE authority roles share a path or inode"
            ):
                fixture.g6.expected_upstream_hce_completion(
                    claim=claim_hardlink,
                    prelabel_seal=prelabel,
                    **common,
                )

    def test_capsule_rejects_shared_target_authority_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(Path(directory))
            capsule_path = fixture.paths["capsule"]
            capsule = json.loads(capsule_path.read_text(encoding="utf-8"))
            hce_claim = Path(capsule["preTargetHceClaim"]["path"])
            capsule["teacherClaim"] = fixture._identity(hce_claim)
            _write_json(capsule_path, capsule)
            preregistration = fixture.namespace / "00-preregistration.json"
            prereg = json.loads(preregistration.read_text(encoding="utf-8"))
            prereg["upstreamCapsule"] = fixture._identity(capsule_path)
            _write_json(preregistration, prereg)
            with self.assertRaisesRegex(
                ValueError,
                "capsule target/HCE authority roles share a path or inode",
            ):
                fixture.g6.verify_canonical_namespace()

    def test_capsule_requires_hce_completion_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(Path(directory))
            capsule = json.loads(fixture.paths["capsule"].read_text(encoding="utf-8"))
            capsule.pop("preTargetHceCompletion")
            _write_json(fixture.paths["capsule"], capsule)
            preregistration = fixture.namespace / "00-preregistration.json"
            prereg = json.loads(preregistration.read_text(encoding="utf-8"))
            prereg["upstreamCapsule"] = fixture._identity(fixture.paths["capsule"])
            _write_json(preregistration, prereg)
            with self.assertRaisesRegex(
                ValueError, "omega-decision-v3 capsule field inventory changed"
            ):
                fixture.g6.verify_canonical_namespace()

    def test_prior_forbidden_overlap_is_rejected_by_fresh_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(Path(directory), forbidden_overlap=True)
            with self.assertRaises(ValueError):
                fixture.g6.verify_canonical_namespace()

    def test_initializer_must_select_first_promoted_catalog_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(
                Path(directory), force_second_promoted_initializer=True
            )
            with self.assertRaises(ValueError):
                fixture.g6.verify_canonical_namespace()

    def test_planned_projection_cannot_point_to_teacher_labels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(
                Path(directory), planned_projection_to_teacher=True
            )
            with self.assertRaises(ValueError):
                fixture.g6.verify_canonical_namespace()

    def test_trainer_cannot_receive_or_fake_expected_batch_digest(self) -> None:
        self.assertNotIn(
            "expected-batch-order-sha256",
            host_g6.TRAINER_COMMAND_PROTOCOL["arguments"],
        )
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(Path(directory), sequential_trainer=True)
            g6 = fixture.g6
            g6.materialize_canonical_authority()
            registry = g6.verify_canonical_namespace()
            expected_digest = g6._training_batch_order_sha256(
                registry,
                "G6A",
                registry.document["primaryTrainingSeeds"]["G6A"],
            )
            command = g6._training_command(
                registry,
                model_id="G6A",
                candidate="G6A",
                purpose="primary-training",
                seed=registry.document["primaryTrainingSeeds"]["G6A"],
                recipe=g6.CANDIDATE_RECIPES["G6A"],
            )
            self.assertNotIn(expected_digest, command)
            claim = g6._training_claim_document(
                registry, "G6A", "2026-07-24T00:00:00.000000Z"
            )
            self.assertNotIn("batchOrderSha256", claim)
            self.assertNotIn(
                expected_digest,
                json.dumps(claim, sort_keys=True, separators=(",", ":")),
            )
            self.assertEqual(
                claim["plannedBatchOrderTranscriptPath"],
                str(g6._canonical_slot(registry, "trainingBatchOrder:G6A")),
            )
            with self.assertRaises(ValueError):
                g6.train_primary("G6A")

    def test_future_metric_preseed_cannot_feed_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(Path(directory))
            g6 = fixture.g6
            g6.materialize_canonical_authority()
            for candidate in g6.CANDIDATES:
                g6.train_primary(candidate)
            metric = g6._canonical_slot(g6.verify_canonical_namespace(), "metric:G6C")
            metric.parent.mkdir(parents=True, exist_ok=True)
            _write_json(metric, {"fabricated": True})
            with self.assertRaises(ValueError):
                g6.evaluate_validation("I0")

    def test_training_history_tamper_breaks_next_stage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(Path(directory))
            g6 = fixture.g6
            g6.materialize_canonical_authority()
            g6.train_primary("G6A")
            registry = g6.verify_canonical_namespace()
            transcript = g6._canonical_slot(
                registry, "trainingBatchOrder:G6A"
            )
            transcript_rows = [
                json.loads(line)
                for line in transcript.read_text(encoding="utf-8").splitlines()
            ]
            self.assertNotEqual(
                transcript_rows[0]["rootIds"],
                sorted(transcript_rows[0]["rootIds"]),
            )
            self.assertEqual(transcript_rows[0]["optimizerStep"], 1)
            self.assertEqual(len(transcript_rows[0]["childIds"]), 256)
            history = g6._canonical_slot(registry, "trainingHistory:G6A")
            document = json.loads(history.read_text(encoding="utf-8"))
            document["batchOrderSha256"] = "0" * 64
            history.write_text(json.dumps(document) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                g6.train_primary("G6B")

    def test_initializer_lineage_substitution_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(Path(directory))
            manifest = json.loads(
                Path(
                    json.loads(
                        (fixture.namespace / "00-preregistration.json").read_text(
                            encoding="utf-8"
                        )
                    )["initializerManifest"]["path"]
                ).read_text(encoding="utf-8")
            )
            selection = Path(manifest["selectionSeal"]["path"])
            _write_json(selection, {"kind": "substituted-generation5-selection"})
            with self.assertRaises(ValueError):
                fixture.g6.verify_canonical_namespace()

    def test_runtime_manifest_substitution_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(Path(directory))
            prereg = json.loads(
                (fixture.namespace / "00-preregistration.json").read_text(
                    encoding="utf-8"
                )
            )
            runtime = Path(prereg["runtimeManifest"]["path"])
            document = json.loads(runtime.read_text(encoding="utf-8"))
            document["numpyVersion"] = "substituted"
            _write_json(runtime, document)
            with self.assertRaises(ValueError):
                fixture.g6.verify_canonical_namespace()

    def test_claim_only_heldout_crash_is_permanently_spent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(Path(directory))
            fixture.run_to_arm()
            g6 = fixture.g6
            registry = g6.verify_canonical_namespace()
            access = g6._verify_canonical_heldout_access(registry)
            claim_path = g6._canonical_slot(registry, "heldoutClaim")
            g6._exclusive_json(
                claim_path,
                g6._heldout_claim_document(
                    registry, access, g6._utc_after(access["createdUtc"])
                ),
            )
            with self.assertRaises(g6.HeldoutAccessError):
                g6.consume_heldout_once()
            self.assertTrue(claim_path.exists())
            self.assertFalse(g6._canonical_slot(registry, "heldoutReport").exists())

    def test_mutating_exported_default_cannot_redirect_production(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(Path(directory))
            fixture.g6.DEFAULT_NAMESPACE_ROOT = Path(directory) / "alternate"
            registry = fixture.g6.verify_canonical_namespace()
            self.assertEqual(registry.namespace, fixture.namespace.resolve())

    @unittest.skipUnless(hasattr(os, "link"), "hard links unavailable")
    def test_namespace_hardlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CanonicalFixture(Path(directory))
            source = fixture.namespace / "00-preregistration.json"
            target = fixture.namespace / "alias.json"
            try:
                os.link(source, target)
            except OSError as error:
                self.skipTest(str(error))
            with self.assertRaises(ValueError):
                fixture.g6.verify_canonical_namespace()

    def test_hostile_arbitrary_cwd_import_and_self_test(self) -> None:
        python = sys.executable
        trainer = Path(host_g6.__file__).resolve()
        test_file = Path(__file__).resolve()
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(
                [python, "-I", "-B", str(trainer), "self-test"],
                cwd=directory,
                check=True,
                capture_output=True,
                text=True,
            )
            completed = subprocess.run(
                [
                    python,
                    "-I",
                    "-B",
                    str(test_file),
                    "Generation6Tests.test_protocol_and_objective_are_exact",
                ],
                cwd=directory,
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
