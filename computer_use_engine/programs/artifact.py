# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read an explicitly selected workspace artifact without importing task code.

v1 is deliberately one reviewed Python file plus a JSON manifest. It is not a
sandbox: the host must authorize the artifact and its imports. No task catalog,
directory scanning, persistent target handles or product task implementations.
"""

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from .admission import canonical, fingerprint


def validate_parameters(schema: dict, values: dict) -> dict:
    """Small explicit v1 schema; reject unsupported rules instead of ignoring them."""
    if not isinstance(values, dict) or set(values) != set(schema):
        raise ValueError("Supply exactly the declared parameters")
    types = {"string": str, "integer": int, "number": (int, float), "boolean": bool}
    for key, spec in schema.items():
        if not isinstance(key, str) or not key or not isinstance(spec, dict):
            raise ValueError("Invalid parameter schema")
        kind = spec.get("type")
        allowed = {"type", "enum"} | (
            {"min_length", "max_length", "pattern"}
            if kind == "string"
            else {"minimum", "maximum"}
            if kind in ("integer", "number")
            else set()
        )
        if kind not in types or set(spec) - allowed:
            raise ValueError("Unsupported parameter type or constraint")
        value = values[key]
        if not isinstance(value, types[kind]) or (kind in ("integer", "number") and isinstance(value, bool)):
            raise ValueError(f"Parameter {key} has the wrong type")
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError(f"Parameter {key} is outside its allowed values")
        if kind == "string":
            if not spec.get("min_length", 0) <= len(value) <= spec.get("max_length", 16384):
                raise ValueError(f"Parameter {key} has an invalid length")
            if "pattern" in spec and re.fullmatch(spec["pattern"], value) is None:
                raise ValueError(f"Parameter {key} does not match its declared pattern")
        if kind in ("integer", "number") and (
            not math.isfinite(value) or not spec.get("minimum", -math.inf) <= value <= spec.get("maximum", math.inf)
        ):
            raise ValueError(f"Parameter {key} is outside its numeric bounds")
    return dict(values)


@dataclass(frozen=True)
class Artifact:
    path: Path
    manifest_json: str
    source: bytes
    runtime_sha256: str

    @classmethod
    def read(cls, path: Path):
        root = path.resolve()
        manifest_path, entry = root / "manifest.json", root / "program.py"
        if manifest_path.is_symlink() or entry.is_symlink():
            raise ValueError("Artifact files must be regular files inside the selected directory")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        required = {"api", "id", "version", "entry", "source_sha256", "parameters", "acceptance", "constraints"}
        if not isinstance(manifest, dict) or set(manifest) != required:
            raise ValueError("Unsupported artifact manifest fields")
        if manifest["api"] != "computer-use.program.v1" or manifest["entry"] != "program.py":
            raise ValueError("Unsupported program contract or entry file")
        if any(not isinstance(manifest[k], str) or not manifest[k].strip() for k in ("id", "version", "constraints")):
            raise ValueError("Artifact identity and constraints must be explicit")
        schema, acceptance = manifest["parameters"], manifest["acceptance"]
        if not isinstance(schema, dict) or not schema:
            raise ValueError("Parameters must be an explicit nonempty schema")
        if not isinstance(acceptance, dict) or set(acceptance) != {
            "condition",
            "scope_parameter",
            "expected_parameters",
        }:
            raise ValueError("Acceptance must declare condition, scope and expected parameters")
        if not isinstance(acceptance["condition"], str) or not acceptance["condition"]:
            raise ValueError("Acceptance must cover declared parameters and a string scope")
        if acceptance["scope_parameter"] not in schema or schema[acceptance["scope_parameter"]].get("type") != "string":
            raise ValueError("Acceptance must cover declared parameters and a string scope")
        if (
            not isinstance(acceptance["expected_parameters"], list)
            or not acceptance["expected_parameters"]
            or any(key not in schema for key in acceptance["expected_parameters"])
        ):
            raise ValueError("Acceptance must cover declared parameters and a string scope")
        source = entry.read_bytes()
        if hashlib.sha256(source).hexdigest() != manifest["source_sha256"]:
            raise ValueError("Artifact source digest does not match its manifest")
        # Changes to generic execution/validation/admission invalidate old review.
        runtime = Path(__file__).parent
        runtime_hashes = {
            name: hashlib.sha256((runtime / name).read_bytes()).hexdigest()
            for name in ("artifact.py", "pipeline.py", "model.py", "admission.py")
        }
        return cls(root, canonical(manifest), source, fingerprint(runtime_hashes))

    @property
    def manifest(self):
        return json.loads(self.manifest_json)

    @property
    def definition(self):
        manifest = self.manifest
        return {
            "id": manifest["id"],
            "version": manifest["version"],
            "implementation_sha256": fingerprint({"manifest": manifest, "runtime_sha256": self.runtime_sha256}),
            "acceptance": manifest["acceptance"]["condition"],
            "constraints": manifest["constraints"],
        }

    def parameters(self, values):
        return validate_parameters(self.manifest["parameters"], values)

    def expectation(self, values):
        acceptance = self.manifest["acceptance"]
        scope = values[acceptance["scope_parameter"]]
        if not scope:
            raise ValueError("Acceptance requires a nonempty scope")
        return scope, fingerprint({key: values[key] for key in acceptance["expected_parameters"]})
