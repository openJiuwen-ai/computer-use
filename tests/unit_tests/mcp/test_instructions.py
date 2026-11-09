# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The delivered prompt must contain an accurate, self-contained SDK reference."""

import ast
import asyncio
import builtins
import re
from importlib.resources import files

import pytest
from computer_use_mcp.instructions import DESCRIPTION
from computer_use_mcp.server import ComputerUseServer
from mcp import types


def api_reference(description):
    blocks = re.findall(r"```python\n(.*?)```", description, re.DOTALL)
    references = [block for block in blocks if "class Computer:" in block]
    assert len(references) == 1, "Deliver the API reference once, without requiring another document read"
    return ast.parse(references[0])


def class_methods(module, name):
    definition = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == name)
    methods = {}
    for node in definition.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            # Computer lifetime belongs to the MCP host, not to model scripts.
            if name == "Computer" and node.name == "close":
                continue
            methods.setdefault(node.name, []).append((
                ast.dump(node.args), ast.dump(node.returns),
                [ast.dump(decorator) for decorator in node.decorator_list],
            ))
    return methods


@pytest.mark.parametrize("name", ["Computer", "App", "Element", "Observation"])
def test_documented_operations_match_the_public_interface(name):
    reference = api_reference(DESCRIPTION)
    public = ast.parse(files("computer_use").joinpath("__init__.pyi").read_text(encoding="utf-8"))
    assert class_methods(reference, name) == class_methods(public, name)


def test_documented_api_is_self_contained():
    reference = api_reference(DESCRIPTION)
    defined = {"self"}
    for node in reference.body:
        if isinstance(node, ast.ImportFrom):
            defined.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.ClassDef):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            defined.update(target.id for target in node.targets if isinstance(target, ast.Name))
    # Type annotations must resolve inside the reference, without an omitted SDK type file.
    defined.update(vars(builtins))
    referenced = {node.id for node in ast.walk(reference) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
    assert referenced <= defined


def test_mcp_tool_list_delivers_the_api_reference():
    server = ComputerUseServer()
    handler = server.server.request_handlers[types.ListToolsRequest]
    result = asyncio.run(handler(types.ListToolsRequest(method="tools/list")))
    tool = next(tool for tool in result.root.tools if tool.name == "computer_use")
    assert tool.description == DESCRIPTION
    api_reference(tool.description)
