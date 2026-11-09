# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Desktop configuration independent of the model's native configuration."""

import sys
from dataclasses import dataclass, field


@dataclass(frozen=True, kw_only=True)
class McpConfig:
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict, repr=False)
    cwd: str | None = None


@dataclass(frozen=True, kw_only=True)
class AgentConfig:
    allow_global_input: bool = True
    mcp: McpConfig | None = None
    max_iterations: int = 40
    context_window: int = 1_000_000
    workspace: str | None = None

    def __post_init__(self):
        if self.allow_global_input is not True and self.allow_global_input is not False:
            raise TypeError("allow_global_input must be a boolean")
        if self.max_iterations < 1 or self.context_window < 1:
            raise ValueError("max_iterations and context_window must be positive")

    def mcp_config(self) -> McpConfig:
        return self.mcp or McpConfig(
            command=sys.executable,
            args=(
                "-m",
                "computer_use_mcp",
                "--allow-global-input" if self.allow_global_input else "--no-allow-global-input",
            ),
        )
