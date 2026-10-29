# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""DeepAgent assembly using the standard openJiuwen tools and context engine."""

from pathlib import Path

from openjiuwen.core.context_engine.processor.forked.compressor.dialogue_compressor import DialogueCompressorConfig
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.harness import DeepAgent
from openjiuwen.harness.factory import apply_deep_agent_parts, resolve_deep_agent_parts
from openjiuwen.harness.rails.context_engineer.context_processor_rail import ContextProcessorRail

from .config import AgentConfig
from .toolkit import ComputerUseToolkit


class ComputerUseAgent(DeepAgent):
    """A DeepAgent with a conversation-scoped Computer Use toolkit.

    invoke, stream and Runner integration are inherited from DeepAgent.
    Use an async context manager or call close when the conversation ends.
    """

    def __init__(
        self,
        model: Model,
        *,
        config: AgentConfig | None = None,
        card: AgentCard | None = None,
        rails=None,
        on_event=None,
        stderr=None,
        **kwargs,
    ):
        config = config or AgentConfig()
        card = card or AgentCard(
            name="computer_use_agent", description="Operate desktop applications using UI observations."
        )
        super().__init__(card)
        self.toolkit = ComputerUseToolkit(config, on_event=on_event, stderr=stderr)
        context = ContextProcessorRail(
            preset=True,
            processors=[
                (
                    "DialogueCompressor",
                    DialogueCompressorConfig(
                        model=model.model_config,
                        model_client=model.model_client_config,
                    ),
                )
            ],
        )
        options = {
            "card": card,
            "tools": [*self.toolkit.tools, *(kwargs.pop("tools", None) or [])],
            "system_prompt": self.toolkit.system_prompt,
            "rails": [context, *(rails or [])],
            "workspace": config.workspace or str(Path.home() / ".computer-use" / "agent" / "workspace"),
            "max_iterations": config.max_iterations,
            "context_engine_config": ContextEngineConfig(context_window_tokens=config.context_window),
            "prompt_mode": "minimal",
            "enable_sys_operation": False,
            "auto_create_workspace": False,
            "enable_read_image_multimodal": True,
            **kwargs,
            "parallel_tool_calls": False,
            "enable_tool_resilience_rail": False,
            "enable_model_anomaly_detection_rail": False,
        }
        apply_deep_agent_parts(self, resolve_deep_agent_parts(model, **options))

    async def compact(self, session=None, *, conversation_id: str | None = None):
        await self.ensure_initialized()
        return await self.react_agent.context_engine.compress_context(session=session, session_id=conversation_id)

    async def close(self):
        await self.toolkit.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.toolkit.__aexit__(exc_type, exc_value, traceback)


def create_agent(model: Model, **kwargs) -> ComputerUseAgent:
    """Create a ComputerUseAgent with standard DeepAgent invocation semantics."""
    return ComputerUseAgent(model, **kwargs)
