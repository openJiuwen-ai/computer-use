# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Desktop configuration independent of the model's native configuration."""

import sys
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, kw_only=True)
class McpConfig:
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict, repr=False)
    cwd: str | None = None


@dataclass(frozen=True, kw_only=True)
class AgentConfig:
    desktop: Literal["shared", "exclusive"] = "exclusive"
    mcp: McpConfig | None = None
    max_iterations: int = 40
    context_window: int = 1_000_000
    workspace: str | None = None
    gaze_connection_file: str | None = None

    def __post_init__(self):
        if self.desktop not in ("shared", "exclusive"):
            raise ValueError("desktop must be shared or exclusive")
        if self.max_iterations < 1 or self.context_window < 1:
            raise ValueError("max_iterations and context_window must be positive")
        if self.gaze_connection_file is not None and (self.mcp is not None or sys.platform != "darwin"):
            raise ValueError("gaze requires the default local macOS MCP connection")

    def mcp_config(self) -> McpConfig:
        return self.mcp or McpConfig(
            command=sys.executable,
            args=("-m", "computer_use_mcp", "--desktop", self.desktop),
        )
