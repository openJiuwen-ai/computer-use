# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""CLI configuration; model objects use openJiuwen's native configuration."""

import json
import os
from importlib.resources import files
from pathlib import Path


def config_path() -> Path:
    return Path.home() / ".computer-use" / "agent" / "config.json"


def initialize(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("x", encoding="utf-8") as stream:
        path.chmod(0o600)
        stream.write(files("computer_use_agent.cli").joinpath("config.example.json").read_text(encoding="utf-8"))


def load(path: Path | None = None, *, overrides=None):
    selected = path or config_path()
    value = json.loads(selected.read_text(encoding="utf-8")) if selected.exists() else {}
    if path is not None and not path.exists():
        raise FileNotFoundError(path)
    if not isinstance(value, dict):
        raise TypeError("Agent configuration must be a JSON object.")
    # Accept existing ReAct model settings at the configuration boundary.
    aliases = {"baseUrl": "api_base", "apiKey": "api_key"}
    for old, new in aliases.items():
        if old in value and new not in value:
            value[new] = value[old]
    if "modelTimeoutMs" in value and "model_timeout" not in value:
        value["model_timeout"] = value["modelTimeoutMs"] / 1000
    if "context_window" not in value and isinstance(value.get("context"), dict):
        value["context_window"] = value["context"].get("windowTokens", 1_000_000)
    environment = {
        "model": os.getenv("MODEL_NAME") or os.getenv("OPENAI_MODEL"),
        "provider": os.getenv("MODEL_PROVIDER"),
        "api_base": os.getenv("API_BASE") or os.getenv("OPENAI_BASE_URL"),
        "api_key": os.getenv("API_KEY") or os.getenv("OPENAI_API_KEY"),
    }
    for key, content in environment.items():
        if content:
            value[key] = content
    value.update({key: content for key, content in (overrides or {}).items() if content is not None})
    if not str(value.get("model", "")).strip():
        raise ValueError(f"Set model in {selected}, pass --model, or set MODEL_NAME.")
    return value


def build(value, *, workspace):
    from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig

    from computer_use_agent import AgentConfig, McpConfig

    native_client = dict(value.get("model_client_config") or {})
    native_client.update(
        {
            "client_provider": value.get("provider", native_client.get("client_provider", "OpenAI")),
            "api_key": value.get("api_key", native_client.get("api_key", "")),
        }
    )
    if "api_base" in value:
        native_client["api_base"] = value["api_base"]
    native_request = {**value.get("model_request_config", {}), "model": value["model"]}
    model = Model(
        model_client_config=ModelClientConfig(**native_client),
        model_config=ModelRequestConfig(**native_request),
    )
    mcp = value.get("mcp")
    config = AgentConfig(
        desktop=value.get("desktop", "exclusive"),
        max_iterations=value.get("max_iterations", 40),
        context_window=value.get("context_window", 1_000_000),
        workspace=str(workspace),
        gaze_connection_file=value.get("gaze_connection_file"),
        mcp=McpConfig(
            command=mcp["command"],
            args=tuple(mcp.get("args", ())),
            env=dict(mcp.get("env", {})),
            cwd=mcp.get("cwd"),
        )
        if mcp
        else None,
    )
    return model, config
