# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OpenJiuwen rails the OSWorld entry point (osworld/vm_agent.py) adds to this repo's agent."""

from osworld.rails.autosave_rail import AutosaveReminderRail
from osworld.rails.benchmark_integrity_rail import BenchmarkIntegrityRail
from osworld.rails.gate_notes_rail import GateNotesRail
from osworld.rails.gui_step_cap_rail import GuiStepCapRail
from osworld.rails.model_fallback_rail import ModelFallbackRail
from osworld.rails.request_size_rail import RequestSizeRail
from osworld.rails.trajectory_rail import TrajectoryStreamRail, dump_trajectory

__all__ = [
    "AutosaveReminderRail",
    "BenchmarkIntegrityRail",
    "GateNotesRail",
    "GuiStepCapRail",
    "ModelFallbackRail",
    "RequestSizeRail",
    "TrajectoryStreamRail",
    "dump_trajectory",
]
