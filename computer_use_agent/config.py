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
class RoutingConfig:
    """Multi-path routing (GUI / CLI / API); see docs/design/multi-path-routing.md."""

    enabled: bool = True
    min_confidence: float = 0.45
    refuse_floor: float = 0.2
    refuse_after_attempts: int = 5
    allow_destructive: bool = False
    time_weight: float = 0.3
    cost_weight: float = 0.2
    exploration: float = 0.3
    time_scale_s: float = 60.0
    cost_scale: float = 1.0
    gui_ops_estimate: int = 8
    paths_file: str | None = None
    metrics_file: str | None = None
    auto_record_gui: bool = True

    def __post_init__(self):
        if self.min_confidence <= 0 or self.min_confidence > 1:
            raise ValueError("min_confidence must be in (0, 1]")
        if self.time_scale_s <= 0 or self.cost_scale < 0:
            raise ValueError("time_scale_s must be positive and cost_scale non-negative")


@dataclass(frozen=True, kw_only=True)
class AgentConfig:
    desktop: Literal["shared", "exclusive"] = "exclusive"
    mcp: McpConfig | None = None
    max_iterations: int = 40
    context_window: int = 1_000_000
    workspace: str | None = None
    routing: RoutingConfig = field(default_factory=RoutingConfig)

    def __post_init__(self):
        if self.desktop not in ("shared", "exclusive"):
            raise ValueError("desktop must be shared or exclusive")
        if self.max_iterations < 1 or self.context_window < 1:
            raise ValueError("max_iterations and context_window must be positive")

    def mcp_config(self) -> McpConfig:
        return self.mcp or McpConfig(
            command=sys.executable,
            args=("-m", "computer_use_mcp", "--desktop", self.desktop),
        )
