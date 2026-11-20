# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import sys

import pytest
from program_fixtures import artifact_at

from computer_use.programs.artifact import Artifact, validate_parameters
from computer_use.programs.pipeline import execute


async def environment():
    return {}


def invoke(artifact, computer, parameters, **kwargs):
    return asyncio.run(
        execute(
            artifact.path, computer, parameters, environment, emit=lambda event: None, checkpoint=lambda: None, **kwargs
        )
    )


def test_v1_runtime_keeps_the_identity_of_its_reviewed_frozen_implementation(tmp_path):
    artifact = artifact_at(tmp_path / "persisted-v1")
    assert artifact.runtime_sha256 == "9a8305771caa1aeb866581c736d84b0dcdbbe61c2dedeb32ef476f936bb3596a"


def test_program_module_is_removed_after_entry_failure(tmp_path):
    artifact = artifact_at(
        tmp_path / "entry-failure",
        source="""import sys

async def run(ctx, computer, parameters):
    computer.append((__name__, sys.modules[__name__].__file__))
    raise OSError("entry failed")
""",
    )
    modules = []
    result = invoke(artifact, modules, dict(document="doc", initial="a", replacement="b"), development=True)

    assert result.status == "stopped"
    assert result.failure["detail"] == "entry failed"
    ((name, file),) = modules
    assert file == str(artifact.path / "program.py")
    assert name not in sys.modules


def test_manifest_tampering_or_file_escape_is_rejected_before_import(tmp_path):
    artifact = artifact_at(tmp_path / "artifact")
    (artifact.path / "program.py").write_text('raise RuntimeError("changed")')
    with pytest.raises(ValueError, match="digest"):
        Artifact.read(artifact.path)
    artifact = artifact_at(artifact.path)
    entry = artifact.path / "program.py"
    external = tmp_path / "outside.py"
    external.write_bytes(entry.read_bytes())
    entry.unlink()
    entry.symlink_to(external)
    with pytest.raises(ValueError, match="regular files"):
        Artifact.read(artifact.path)


def test_parameter_validation_preserves_python_names_and_mapping_pairs():
    class Schema(dict):
        def items(self):
            return [list(pair) for pair in super().items()]

    class Name(str):
        def __format__(self, _spec):
            return "formatted"

    assert validate_parameters(Schema(value={"type": "integer"}), {"value": 1}) == {"value": 1}
    with pytest.raises(ValueError) as formatted:
        validate_parameters({Name("value"): {"type": "integer"}}, {"value": "text"})
    assert formatted.value.args == ("Parameter formatted has the wrong type",)
    with pytest.raises(ValueError) as surrogate:
        validate_parameters({"\ud800": {"type": "integer"}}, {"\ud800": "text"})
    assert surrogate.value.args == ("Parameter \ud800 has the wrong type",)
