# DeepAgent: build, install and integrate from source

[Back to README](../README.md) | [简体中文](deepagent.md) | [MCP integration](mcp.en.md)

Integrate Computer Use DeepAgent into an application, or add its desktop tools to an existing openJiuwen DeepAgent.

## 1. Prepare the environment

Install Python 3.11, 3.12 or 3.13 and `uv`. Build and run the desktop operation service on the computer whose desktop it will operate.

Prepare the native dependencies on the computer whose desktop you will operate:

| Platform | Requirements |
| --- | --- |
| macOS | macOS 14 or later, an Xcode toolchain with Swift 6, Accessibility and Screen Recording permissions. |
| Windows | Windows x64 with .NET Framework 4.8 and its C# compiler; window capture requires Windows 10 1903 or later. |
| Linux | A graphical desktop session with AT-SPI and PyGObject. Use X11 for screenshots and mouse input. |

On Debian or Ubuntu, install the Linux system dependencies first:

```bash
sudo apt install python3-gi python3-venv gir1.2-atspi-2.0 libx11-6 libxkbcommon0
```

## 2. Install from source and build the backend

```bash
git clone https://gitcode.com/openJiuwen/computer-use.git
cd computer-use
uv sync --python 3.12 --no-dev --extra agent
```

The SDK, MCP and Agent code are installed in editable mode into the checkout's `.venv`, together with openJiuwen and other Agent dependencies. This command selects Python 3.12. Use `--python 3.11` or `--python 3.13` to match an existing environment. Run the following commands from the checkout root.

Build or prepare the native backend, then check desktop access:

```bash
uv run --no-dev --extra agent computer-use-build
uv run --no-dev --extra agent computer-use-permissions
```

On macOS, use `uv run --no-dev --extra agent computer-use-permissions --request` to request missing permissions. Approve them in **System Settings → Privacy & Security**, then run the check again. Run the commands from the same terminal or host connection that will launch the SDK or its MCP adapter.

## 3. Create a desktop agent

For a wheel installation, select the `agent` extra instead; it includes MCP.

Configure a model that supports tool calls and image input using openJiuwen's `ModelClientConfig` and `ModelRequestConfig`. The example reads the provider, endpoint, credentials and model name from `MODEL_PROVIDER`, `API_BASE`, `API_KEY` and `MODEL_NAME`.

`ComputerUseAgent` inherits the standard DeepAgent `invoke()` and `stream()` interfaces and works with `Runner`. Pass a native openJiuwen `Model` and retain `conversation_id` for follow-up messages:

```python
import asyncio
import os

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig

from computer_use_agent import create_agent


async def main():
    model = Model(
        model_client_config=ModelClientConfig(
            client_provider=os.getenv("MODEL_PROVIDER", "OpenAI"),
            api_base=os.environ["API_BASE"],
            api_key=os.environ["API_KEY"],
        ),
        model_config=ModelRequestConfig(model=os.environ["MODEL_NAME"]),
    )
    async with create_agent(model) as agent:
        conversation_id = "desktop-conversation"
        print(await agent.invoke({"query": "List the running applications", "conversation_id": conversation_id}))
        async for chunk in agent.stream({"query": "Inspect the calculator window", "conversation_id": conversation_id}):
            print(chunk)


asyncio.run(main())
```

Save the complete example above as `desktop_agent.py` and run it from the source checkout:

```bash
uv run --no-dev --extra agent python desktop_agent.py
```

First check application enumeration, then use the streaming call to inspect actual window text and screenshots. Enumeration only verifies local backend calls; desktop access also requires the relevant permissions and successful observations.

`AgentConfig.allow_global_input` defaults to `True`. Set it to `False` to restrict global input in the local MCP session. Set `AgentConfig.mcp` to a `McpConfig` to connect to another MCP host. The async context manager closes the computer-operation session when the conversation ends.

## 4. Integrate an existing openJiuwen project

The host project must also use Python 3.11, 3.12 or 3.13. In its directory, add this checkout as an editable dependency to the host environment:

```bash
uv add --editable /absolute/path/to/computer-use --extra agent
```

Replace the path with the checkout whose native backend you built above. For a pip-managed host environment, run `python -m pip install -e "/absolute/path/to/computer-use[agent]"` instead. Reuse the project's model configuration. The default MCP subprocess uses the host Python environment, so these dependencies must also be installed there.

Existing openJiuwen DeepAgent applications can also add the standard tools provided by `ComputerUseToolkit`:

```python
from openjiuwen.harness import create_deep_agent

from computer_use_agent import ComputerUseToolkit


async def run(model):
    async with ComputerUseToolkit() as desktop:
        agent = create_deep_agent(
            model,
            tools=desktop.tools,
            system_prompt=desktop.system_prompt,
            parallel_tool_calls=False,
            enable_tool_resilience_rail=False,
            enable_sys_operation=False,
            enable_read_image_multimodal=True,
        )
        return await agent.invoke({"query": "List the running applications"})
```

## 5. Run directly with the CLI

The same agent can also run from the CLI. First run `uv run --extra agent computer-use-agent --init-config` and fill in `model`, `provider`, `api_base` and `api_key` in `~/.computer-use/agent/config.json`. Start a conversation or pass a task to execute once:

```bash
uv run --extra agent computer-use-agent
uv run --extra agent computer-use-agent "Open the meeting scheduling form and stop before submitting"
```

Run `uv run --extra agent computer-use-agent --help` for CLI options.

## 6. Update the source

After changing Python source, restart the host application or recreate the Agent/Toolkit session. After dependency or entry-point changes, rerun `uv sync --no-dev --extra agent` in the checkout and update the host project dependencies. After native backend changes, run `uv run --no-dev --extra agent computer-use-build`, then restart the session.

See the [MCP interface reference](mcp.en.md#4-interface-reference) for engine calls and observations, and the [packaging guide](../PACKAGING.md) for development packages.
