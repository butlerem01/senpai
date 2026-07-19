#!/usr/bin/env python3
"""Label Omega NNUE JSONL records with Senpai's handcrafted evaluator.

The C++ test helper stays alive for the complete input and evaluates each
six-field Omega OFEN with ``UseOmegaNNUE=false``.  Every output row preserves
the input object and replaces or adds the side-to-move ``targetCpStm`` field.
The labeled JSONL and its content-pinning manifest are written atomically.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, BinaryIO


INTEGER_OUTPUT = re.compile(r"[+-]?\d+\Z")
HELPER_MODE = "--evaluate-handcrafted-stream"

INITIAL_OFEN = (
    "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/"
    "PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1"
)
PAWN_OFEN_WHITE = (
    "5k4/10/10/10/10/4P5/10/10/10/4K5[-/-/-/-] w - - 0 1"
)
PAWN_OFEN_BLACK = PAWN_OFEN_WHITE.replace(" w - - ", " b - - ")


def _file_pin(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    digest = hashlib.sha256()
    byte_count = 0
    with resolved.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
            byte_count += len(block)
    return {
        "path": str(resolved),
        "bytes": byte_count,
        "sha256": digest.hexdigest(),
    }


def _same_pin(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left["path"] == right["path"]
        and left["bytes"] == right["bytes"]
        and left["sha256"] == right["sha256"]
    )


def _open_temporary(path: Path) -> tuple[str, BinaryIO]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    return name, os.fdopen(handle, "wb")


def _stage_bytes(path: Path, payload: bytes) -> str:
    name, stream = _open_temporary(path)
    try:
        with stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return name
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def _cleanup_temporary(name: str | None) -> None:
    if name is None:
        return
    try:
        os.unlink(name)
    except OSError:
        pass


def _normalized_ofen(record: dict[str, Any]) -> tuple[str, str]:
    value = record.get("ofen")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing nonempty 'ofen'")
    fields = value.split()
    if len(fields) != 6:
        raise ValueError(f"expected six-field Omega OFEN, got {len(fields)} fields")
    side = fields[1].lower()
    if side not in ("w", "b"):
        raise ValueError(f"invalid OFEN side to move {fields[1]!r}")

    supplied_side = record.get("sideToMove")
    if supplied_side is not None:
        normalized_side = str(supplied_side).strip().lower()
        normalized_side = {"white": "w", "black": "b"}.get(
            normalized_side, normalized_side
        )
        if normalized_side != side:
            raise ValueError(
                f"sideToMove {supplied_side!r} disagrees with OFEN"
            )
    return " ".join(fields), side


class HandcraftedEvaluator:
    def __init__(self, executable: Path) -> None:
        self.executable = executable.resolve(strict=True)
        self.process: subprocess.Popen[str] | None = None

    def __enter__(self) -> "HandcraftedEvaluator":
        self.process = subprocess.Popen(
            [str(self.executable), HELPER_MODE],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            bufsize=1,
        )
        return self

    def evaluate(self, ofen: str, location: str) -> int:
        process = self._required_process()
        assert process.stdin is not None
        assert process.stdout is not None
        try:
            process.stdin.write(ofen + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise RuntimeError(
                f"{location}: handcrafted evaluator stopped accepting input"
                f"{self._failure_detail()}"
            ) from error

        response = process.stdout.readline()
        if response == "":
            raise RuntimeError(
                f"{location}: handcrafted evaluator emitted no score"
                f"{self._failure_detail()}"
            )
        text = response.rstrip("\r\n")
        if INTEGER_OUTPUT.fullmatch(text) is None:
            raise RuntimeError(
                f"{location}: handcrafted evaluator did not emit one bare "
                f"integer: {text!r}"
            )
        return int(text)

    def finish(self) -> None:
        process = self._required_process()
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        process.stdin.close()
        extra_output = process.stdout.read()
        try:
            return_code = process.wait(timeout=30)
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.wait()
            raise RuntimeError(
                "handcrafted evaluator did not exit after end of input"
            ) from error
        error_output = process.stderr.read().strip()
        if return_code != 0:
            detail = error_output or extra_output.strip() or "no diagnostic"
            raise RuntimeError(
                f"handcrafted evaluator exited {return_code}: {detail}"
            )
        if extra_output:
            raise RuntimeError(
                "handcrafted evaluator emitted more scores than input records"
            )

    def _required_process(self) -> subprocess.Popen[str]:
        if self.process is None:
            raise RuntimeError("handcrafted evaluator is not running")
        return self.process

    def _failure_detail(self) -> str:
        process = self._required_process()
        return_code = process.poll()
        if return_code is None:
            return ""
        assert process.stderr is not None
        diagnostic = process.stderr.read().strip()
        suffix = f" (exit {return_code}"
        if diagnostic:
            suffix += f": {diagnostic}"
        return suffix + ")"

    def __exit__(self, exception_type: Any, exception: Any, traceback: Any) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def label_jsonl(
    *,
    input_path: Path,
    output_path: Path,
    manifest_path: Path,
    evaluator_path: Path,
) -> dict[str, Any]:
    source = input_path.resolve(strict=True)
    output = output_path.resolve()
    manifest_target = manifest_path.resolve()
    evaluator_before = _file_pin(evaluator_path)
    tool_before = _file_pin(Path(__file__))

    if source == output:
        raise ValueError("input and output paths must be different")
    if manifest_target in (source, output):
        raise ValueError("manifest path must differ from input and output")

    snapshot_bytes = source.stat().st_size
    input_digest = hashlib.sha256()
    output_digest = hashlib.sha256()
    output_bytes = 0
    records = 0
    blank_lines = 0
    replaced_targets = 0
    minimum_score: int | None = None
    maximum_score: int | None = None
    score_sum = 0
    output_temporary: str | None = None
    manifest_temporary: str | None = None

    output_temporary, output_stream = _open_temporary(output)
    try:
        with output_stream, source.open("rb") as input_stream, HandcraftedEvaluator(
            evaluator_path
        ) as evaluator:
            consumed = 0
            line_number = 0
            while consumed < snapshot_bytes:
                raw_line = input_stream.readline(snapshot_bytes - consumed)
                if not raw_line:
                    raise RuntimeError(
                        f"{source} became shorter while its snapshot was read"
                    )
                consumed += len(raw_line)
                input_digest.update(raw_line)
                line_number += 1
                if not raw_line.strip():
                    output_stream.write(raw_line)
                    output_digest.update(raw_line)
                    output_bytes += len(raw_line)
                    blank_lines += 1
                    continue

                encoding = "utf-8-sig" if line_number == 1 else "utf-8"
                try:
                    decoded = raw_line.decode(encoding)
                    value = json.loads(decoded)
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ValueError(
                        f"{source}:{line_number}: invalid JSONL: {error}"
                    ) from error
                if not isinstance(value, dict):
                    raise ValueError(
                        f"{source}:{line_number}: JSONL row is not an object"
                    )
                try:
                    ofen, _side = _normalized_ofen(value)
                except ValueError as error:
                    raise ValueError(
                        f"{source}:{line_number}: {error}"
                    ) from error

                score = evaluator.evaluate(
                    ofen, f"{source}:{line_number}"
                )
                if "targetCpStm" in value:
                    replaced_targets += 1
                value["targetCpStm"] = score
                try:
                    encoded = (
                        json.dumps(
                            value,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            allow_nan=False,
                        )
                        + "\n"
                    ).encode("utf-8")
                except (TypeError, ValueError) as error:
                    raise ValueError(
                        f"{source}:{line_number}: record is not strict JSON: "
                        f"{error}"
                    ) from error
                output_stream.write(encoded)
                output_digest.update(encoded)
                output_bytes += len(encoded)
                records += 1
                score_sum += score
                minimum_score = (
                    score if minimum_score is None else min(minimum_score, score)
                )
                maximum_score = (
                    score if maximum_score is None else max(maximum_score, score)
                )
            if consumed != snapshot_bytes:
                raise AssertionError("input snapshot byte count drifted")
            if records == 0:
                raise ValueError(f"{source}: no JSONL records were found")
            evaluator.finish()
            output_stream.flush()
            os.fsync(output_stream.fileno())

        evaluator_after = _file_pin(evaluator_path)
        tool_after = _file_pin(Path(__file__))
        if not _same_pin(evaluator_before, evaluator_after):
            raise RuntimeError("handcrafted evaluator changed while labeling")
        if not _same_pin(tool_before, tool_after):
            raise RuntimeError("label_hce.py changed while labeling")

        output_pin = {
            "path": str(output),
            "bytes": output_bytes,
            "sha256": output_digest.hexdigest(),
        }
        manifest = {
            "schemaVersion": 1,
            "kind": "omega-hce-static-labels",
            "label": {
                "field": "targetCpStm",
                "perspective": "side-to-move",
                "units": "centipawns",
                "source": "Senpai Omega handcrafted evaluator",
                "useOmegaNNUE": False,
                "helperMode": HELPER_MODE,
            },
            "input": {
                "path": str(source),
                "snapshotBytes": snapshot_bytes,
                "snapshotSha256": input_digest.hexdigest(),
            },
            "output": output_pin,
            "evaluator": evaluator_before,
            "tool": tool_before,
            "records": {
                "labeled": records,
                "blankLinesPreserved": blank_lines,
                "existingTargetsReplaced": replaced_targets,
            },
            "scores": {
                "minimumCpStm": minimum_score,
                "maximumCpStm": maximum_score,
                "meanCpStm": score_sum / records,
            },
            "environment": {
                "python": sys.version,
                "pythonExecutable": str(Path(sys.executable).resolve()),
                "platform": platform.platform(),
            },
            "atomicWrite": {
                "stagedBeforeCommit": ["output", "manifest"],
                "commitOrder": ["output", "manifest"],
            },
        }
        manifest_payload = (
            json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n"
        ).encode("utf-8")
        manifest_temporary = _stage_bytes(manifest_target, manifest_payload)

        os.replace(output_temporary, output)
        output_temporary = None
        os.replace(manifest_temporary, manifest_target)
        manifest_temporary = None
        return manifest
    finally:
        _cleanup_temporary(output_temporary)
        _cleanup_temporary(manifest_temporary)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as stream:
        for line in stream:
            if line.strip():
                value = json.loads(line)
                assert isinstance(value, dict)
                records.append(value)
    return records


def run_self_test(evaluator_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="omega-hce-label-test-") as directory:
        root = Path(directory)
        source = root / "input.jsonl"
        output = root / "output.jsonl"
        manifest_path = root / "output.jsonl.manifest.json"
        original = [
            {
                "schemaVersion": 1,
                "sampleId": "symmetric-start",
                "groupId": "opening-family-1",
                "ofen": INITIAL_OFEN,
                "sideToMove": "white",
                "targetCpStm": 123456,
                "outcomeStm": 0.5,
                "provenance": {"gameId": "g-1", "tags": ["smoke", 7]},
            },
            {
                "sampleId": "white-pawn-white-turn",
                "groupId": "material-family-1",
                "ofen": PAWN_OFEN_WHITE,
                "sideToMove": "w",
                "provenance": {"gameId": "g-2"},
            },
            {
                "sampleId": "white-pawn-black-turn",
                "groupId": "material-family-1",
                "ofen": PAWN_OFEN_BLACK,
                "sideToMove": "black",
                "provenance": {"gameId": "g-2"},
            },
        ]
        source.write_text(
            "".join(
                json.dumps(row, separators=(",", ":")) + "\n"
                for row in original
            ),
            encoding="utf-8",
            newline="\n",
        )
        manifest = label_jsonl(
            input_path=source,
            output_path=output,
            manifest_path=manifest_path,
            evaluator_path=evaluator_path,
        )
        labeled = _read_jsonl(output)
        if len(labeled) != len(original):
            raise AssertionError("self-test did not preserve the record count")
        for before, after in zip(original, labeled):
            expected = dict(before)
            expected["targetCpStm"] = after["targetCpStm"]
            if after != expected:
                raise AssertionError(
                    "self-test changed a field other than targetCpStm"
                )
            if not isinstance(after["targetCpStm"], int):
                raise AssertionError("self-test label is not an integer")
        white_pawn = labeled[1]["targetCpStm"]
        black_pawn = labeled[2]["targetCpStm"]
        # Senpai includes a side-to-move tempo term, so opposite turns need
        # not be exact negations. The extra White pawn must nevertheless be
        # favorable to White and unfavorable to Black.
        if white_pawn <= 0 or black_pawn >= 0:
            raise AssertionError(
                "side-to-move HCE labels did not preserve score perspective"
            )
        output_bytes = output.read_bytes()
        if hashlib.sha256(output_bytes).hexdigest() != manifest["output"]["sha256"]:
            raise AssertionError("self-test output hash does not match manifest")
        if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
            raise AssertionError("self-test manifest round trip changed content")

        sentinel = b"do-not-replace-on-failure\n"
        output.write_bytes(sentinel)
        invalid = root / "invalid.jsonl"
        invalid.write_text(
            json.dumps(
                {
                    "ofen": "not an OFEN",
                    "groupId": "invalid",
                }
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        try:
            label_jsonl(
                input_path=invalid,
                output_path=output,
                manifest_path=manifest_path,
                evaluator_path=evaluator_path,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("self-test accepted malformed OFEN input")
        if output.read_bytes() != sentinel:
            raise AssertionError("failed labeling replaced the previous output")

        invalid_helper = subprocess.run(
            [str(evaluator_path.resolve(strict=True)), HELPER_MODE],
            input="not an Omega OFEN\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        if invalid_helper.returncode != 5:
            raise AssertionError(
                "C++ stream helper did not reject malformed OFEN input"
            )
        if "input line 1" not in invalid_helper.stderr:
            raise AssertionError(
                "C++ stream helper did not identify the malformed input line"
            )

    print("Omega HCE JSONL labeling self-test passed", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="trainer-compatible JSONL")
    parser.add_argument("--output", type=Path, help="labeled JSONL")
    parser.add_argument(
        "--manifest",
        type=Path,
        help="manifest path (default: <output>.manifest.json)",
    )
    parser.add_argument(
        "--cpp-evaluator",
        required=True,
        type=Path,
        help="omega_nnue test helper executable",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run a temporary end-to-end smoke test",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    try:
        if args.self_test:
            if args.input is not None or args.output is not None:
                parser.error("--self-test cannot be combined with --input/--output")
            run_self_test(args.cpp_evaluator)
            return 0
        if args.input is None or args.output is None:
            parser.error("--input and --output are required unless --self-test is used")
        manifest_path = args.manifest or args.output.with_suffix(
            args.output.suffix + ".manifest.json"
        )
        manifest = label_jsonl(
            input_path=args.input,
            output_path=args.output,
            manifest_path=manifest_path,
            evaluator_path=args.cpp_evaluator,
        )
        if not args.quiet:
            print(
                f"labeled {manifest['records']['labeled']} records: "
                f"{Path(manifest['output']['path'])}",
                flush=True,
            )
            print(
                f"output sha256: {manifest['output']['sha256']}",
                flush=True,
            )
            print(f"wrote manifest: {manifest_path.resolve()}", flush=True)
        return 0
    except (AssertionError, OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
