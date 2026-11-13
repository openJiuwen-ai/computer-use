# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Draft and publish exact program snapshots in the Agent's local library."""

import hashlib
import json
import os
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from . import calc, workflow
from .artifact import Artifact, canonical, fingerprint

_VERSION = re.compile(r"[0-9a-f]{32}")
_WORKFLOW_ID = re.compile(r"workflow-[0-9a-f]{16}")


def _check_evidence(artifact, report):
    if report.get("definition") != artifact.definition:
        raise ValueError("Validation did not cover this program revision")
    source = report.get("source", {})
    if source.get("status") != "succeeded":
        raise ValueError("The source task's saved outcome could not be confirmed")
    provenance = json.loads((artifact.path / "source.json").read_text(encoding="utf-8"))
    if artifact.definition["acceptance"] == workflow.ACCEPTANCE:
        metadata = _check_function_source(artifact, source, provenance)
        cases = report.get("cases", [])
        expected = "live_read" if metadata["read_only"] else "requested_run"
        if len(cases) != 1 or cases[0].get("case") != expected:
            raise ValueError("The generated function still needs a verified execution")
        case = cases[0]
        parameters = artifact.parameters(case.get("parameters"))
        if not workflow.confirmed(artifact, parameters, case):
            raise ValueError("Execution did not verify this function revision and input")
        if case.get("effect") != ("none" if metadata["read_only"] else "confirmed"):
            raise ValueError("The verified execution did not exercise the function's intended effect")
        return
    if artifact.definition["acceptance"] != calc.ACCEPTANCE:
        raise ValueError("This revision uses an older format. Compile the task again to generate a function.")
    if source.get("parameters") != provenance["parameters"]:
        raise ValueError("Validation did not cover the source task parameters")
    expected = ("positive", "different_parameters", "missing_sheet")
    cases = report.get("cases", [])
    if tuple(case.get("case") for case in cases) != expected:
        raise ValueError("Validation requires two positive cases and the missing-sheet case")
    first, second, negative = cases
    for case in (first, second):
        if case.get("status") != "succeeded" or case.get("acceptance", {}).get("value") != "TRUE":
            raise ValueError(f"Validation failed for {case['case']}: {case.get('detail', 'unconfirmed')}")
    for key in ("document", "sheet", "range", "decimals"):
        if first["parameters"][key] == second["parameters"][key]:
            raise ValueError(f"Positive fixtures must vary {key}")
    if negative.get("status") != "refused" or negative.get("effect") != "none" or negative.get("unchanged") is not True:
        raise ValueError(f"Missing-sheet case did not refuse before input: {negative.get('detail', '')}")
    for case in cases:
        parameters = artifact.parameters(case["parameters"])
        scope, expected_hash = artifact.expectation(parameters)
        acceptance = case.get("acceptance")
        if not acceptance:
            if case is negative:
                continue
            raise ValueError("Missing case acceptance")
        evidence = acceptance["evidence"]
        if (
            acceptance["condition"] != artifact.definition["acceptance"]
            or acceptance["scope"] != scope
            or evidence["execution_id"] != case["execution_id"]
            or evidence["expected_sha256"] != expected_hash
        ):
            raise ValueError("Validation receipt did not match its fixture and execution")


def _check_function_source(artifact, source, provenance):
    if source.get("evidence_sha256") != fingerprint(provenance):
        raise ValueError("Validation did not cover the recorded task")
    return workflow.function_from(artifact)


class Library:
    def __init__(self, root: Path):
        self.root = root.expanduser()

    def draft(self, source_task_id: str, parameters: dict) -> Artifact:
        version = uuid4().hex
        source = calc.PROGRAM.encode()
        manifest = {
            "api": "computer-use.program.v1",
            "id": calc.ID,
            "version": version,
            "entry": "program.py",
            "source_sha256": hashlib.sha256(source).hexdigest(),
            "parameters": calc.PARAMETERS,
            "acceptance": {
                "condition": calc.ACCEPTANCE,
                "scope_parameter": "document",
                "expected_parameters": list(calc.PARAMETERS),
            },
            "constraints": calc.revision(),
        }
        path = self.root / "drafts" / version
        path.mkdir(parents=True, mode=0o700)
        (path / "manifest.json").write_text(canonical(manifest) + "\n", encoding="utf-8")
        (path / "program.py").write_bytes(source)
        (path / "source.json").write_text(
            canonical({"task_id": source_task_id, "parameters": parameters}) + "\n", encoding="utf-8"
        )
        return Artifact.read(path)

    def record_source(self, evidence):
        path = self.root / "drafts" / uuid4().hex
        path.mkdir(parents=True, mode=0o700)
        (path / "source.json").write_text(canonical(evidence) + "\n", encoding="utf-8")
        return path

    def function_draft(self, path, proposal, *, read_only):
        metadata, examples = workflow.prepare(proposal, read_only=read_only)
        source = workflow.program(proposal["code"], metadata)
        digest = hashlib.sha256(source).hexdigest()
        program_id = f"workflow-{digest[:16]}"
        parameters = {name: {"type": spec["type"]} for name, spec in metadata["parameters"].items()}
        schema = {workflow.SCOPE: {"type": "string", "enum": [program_id]}, **parameters}
        manifest = {
            "api": "computer-use.program.v1",
            "id": program_id,
            "version": path.name,
            "entry": "program.py",
            "source_sha256": digest,
            "parameters": schema,
            "acceptance": {
                "condition": workflow.ACCEPTANCE,
                "scope_parameter": workflow.SCOPE,
                "expected_parameters": list(schema),
            },
            "constraints": workflow.revision(),
        }
        (path / "manifest.json").write_text(canonical(manifest) + "\n", encoding="utf-8")
        (path / "program.py").write_bytes(source)
        (path / "README.md").write_text(workflow.readme(metadata), encoding="utf-8")
        return Artifact.read(path), metadata, examples

    def save_check(self, path, report):
        with TemporaryDirectory(dir=path, prefix=".check-") as directory:
            pending = Path(directory) / "check.json"
            pending.write_text(canonical(report) + "\n", encoding="utf-8")
            pending.replace(path / "check.json")

    def lookup_draft(self, draft_id):
        if not isinstance(draft_id, str) or not draft_id.startswith("draft-"):
            return None
        version = draft_id.removeprefix("draft-")
        if _VERSION.fullmatch(version) is None:
            return None
        path = self.root / "drafts" / version
        try:
            report = json.loads((path / "check.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if report.get("status") != "ready_to_validate":
            return None
        artifact = Artifact.read(path)
        source = report.get("source", {})
        if report.get("definition") != artifact.definition or source.get("status") != "succeeded":
            raise ValueError("Draft validation does not cover the selected revision")
        _check_function_source(artifact, source, json.loads((path / "source.json").read_text(encoding="utf-8")))
        return artifact, report

    def candidates(self):
        checks = (self.root / "drafts").glob("*/check.json")
        return [
            f"draft-{path.parent.name}" for path in sorted(checks, key=lambda file: file.stat().st_mtime, reverse=True)
        ]

    def publish(self, draft: Artifact, report: dict):
        if draft.definition["acceptance"] != workflow.ACCEPTANCE:
            self.save_check(draft.path, report)
        _check_evidence(draft, report)
        parent = self.root / "published" / draft.definition["id"]
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        version = draft.definition["version"]
        destination = parent / version
        with TemporaryDirectory(dir=parent, prefix=".publish-") as staging:
            temporary = Path(staging)
            snapshot = temporary / "revision"
            snapshot.mkdir(mode=0o700)
            (snapshot / "manifest.json").write_text(draft.manifest_json + "\n", encoding="utf-8")
            (snapshot / "program.py").write_bytes(draft.source)
            (snapshot / "check.json").write_text(canonical(report) + "\n", encoding="utf-8")
            (snapshot / "source.json").write_bytes((draft.path / "source.json").read_bytes())
            if (draft.path / "README.md").is_file():
                (snapshot / "README.md").write_bytes((draft.path / "README.md").read_bytes())
            try:
                snapshot.rename(destination)
            except OSError:
                if not destination.is_dir():
                    raise
                existing = self.lookup(draft.definition["id"], version)
                if existing is None or existing.definition != draft.definition:
                    raise
            pointer = temporary / "current"
            pointer.write_text(
                canonical({"version": version, "definition": fingerprint(draft.definition)}) + "\n", encoding="utf-8"
            )
            os.replace(pointer, parent / "current.json")
        if draft.definition["acceptance"] == workflow.ACCEPTANCE:
            self.save_check(draft.path, {**report, "status": "published"})
        return Artifact.read(destination)

    def lookup(self, program_id: str = calc.ID, version: str | None = None) -> Artifact | None:
        if not isinstance(program_id, str) or (program_id != calc.ID and _WORKFLOW_ID.fullmatch(program_id) is None):
            return None
        parent = self.root / "published" / program_id
        current = None
        if version is None:
            try:
                current = json.loads((parent / "current.json").read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None
            version = current["version"]
        if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
            raise ValueError("Invalid program version")
        path = parent / version
        artifact = Artifact.read(path)
        if artifact.definition["id"] != program_id or artifact.definition["version"] != version:
            raise ValueError("Published program identity does not match its path")
        if current and current["definition"] != fingerprint(artifact.definition):
            raise ValueError("Published program runtime has changed; compile again")
        if program_id == calc.ID and artifact.manifest["constraints"] != calc.revision():
            raise ValueError("Published program implementation has changed; compile again")
        _check_evidence(artifact, json.loads((path / "check.json").read_text(encoding="utf-8")))
        return artifact

    def entries(self):
        pointers = (self.root / "published").glob("*/current.json")
        return [
            pointer.parent.name for pointer in sorted(pointers, key=lambda file: file.stat().st_mtime, reverse=True)
        ]
