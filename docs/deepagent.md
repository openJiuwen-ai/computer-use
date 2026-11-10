# DeepAgent：从源码构建、安装与接入

[返回 README](../README_CN.md) | [English](deepagent.en.md) | [MCP 接入](mcp.md)

将 Computer Use DeepAgent 集成到应用中，或为已有 openJiuwen DeepAgent 增加电脑操作工具。

## 1. 准备环境

安装 Python 3.11、3.12 或 3.13 及 `uv`。构建和电脑操作服务均在被操作桌面的电脑上运行。

在需要操作桌面的目标电脑上准备原生依赖：

| 平台 | 环境要求 |
| --- | --- |
| macOS | macOS 14 或更高版本、包含 Swift 6 的 Xcode 工具链，以及辅助功能和屏幕录制权限。 |
| Windows | Windows x64、.NET Framework 4.8 及其 C# 编译器；窗口截图需要 Windows 10 1903 或更高版本。 |
| Linux | 包含 AT-SPI 和 PyGObject 的图形桌面会话；截图和鼠标输入使用 X11。 |

Debian 或 Ubuntu 需要先安装 Linux 系统依赖：

```bash
sudo apt install python3-gi python3-venv gir1.2-atspi-2.0 libx11-6 libxkbcommon0
```

## 2. 安装源码并构建后端

```bash
git clone https://gitcode.com/openJiuwen/computer-use.git
cd computer-use
uv sync --python 3.12 --no-dev --extra agent
```

SDK、MCP 和 Agent 代码以 editable 方式安装到仓库的 `.venv`，同时安装 openJiuwen 等 Agent 依赖。此命令选择 Python 3.12。已有 Python 3.11 或 3.13 环境也可选择对应版本。以下命令均在仓库根目录执行。

构建或准备原生后端，并检查桌面访问条件：

```bash
uv run --no-dev --extra agent computer-use-build
uv run --no-dev --extra agent computer-use-permissions
```

macOS 可运行 `uv run --no-dev --extra agent computer-use-permissions --request` 请求缺失权限，在**系统设置 → 隐私与安全**中批准后重新检查。请使用与启动 SDK 或其 MCP 接入层相同的终端或宿主连接执行这些命令。

## 3. 创建电脑操作 Agent

通过 wheel 安装时选择 `agent` extra；其中已经包含 MCP。

通过 openJiuwen 的 `ModelClientConfig`、`ModelRequestConfig` 配置支持工具调用和图片输入的模型。以下示例从 `MODEL_PROVIDER`、`API_BASE`、`API_KEY`、`MODEL_NAME` 环境变量读取服务提供方、地址、密钥和模型名称。

`ComputerUseAgent` 继承 DeepAgent 的标准 `invoke()`、`stream()` 接口，也可交给 `Runner` 执行。模型使用 openJiuwen 原生配置构建，后续消息复用 `conversation_id`：

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
        print(await agent.invoke({"query": "列出正在运行的应用", "conversation_id": conversation_id}))
        async for chunk in agent.stream({"query": "查看计算器窗口", "conversation_id": conversation_id}):
            print(chunk)


asyncio.run(main())
```

将上面的完整示例保存为 `desktop_agent.py`，在源码仓库根目录运行：

```bash
uv run --no-dev --extra agent python desktop_agent.py
```

先核对应用枚举结果，再用流式调用检查实际窗口文字和截图。应用枚举成功仅验证本机后端调用，完整桌面访问还需所需权限和实际观察结果。

`AgentConfig.allow_global_input` 默认开启。设为 `False` 可限制本地 MCP 会话的全局输入。通过 `AgentConfig.mcp` 配置 `McpConfig` 可连接其他 MCP 宿主。异步上下文在对话结束时清理电脑操作会话。

## 4. 接入已有 openJiuwen 项目

宿主项目同样使用 Python 3.11、3.12 或 3.13。在宿主项目目录中，将本仓库作为 editable 依赖安装到宿主项目的环境：

```bash
uv add --editable /absolute/path/to/computer-use --extra agent
```

将路径替换为已完成后端构建的源码仓库路径。使用 pip 管理环境时，在宿主环境中运行 `python -m pip install -e "/absolute/path/to/computer-use[agent]"`。随后复用项目已有的模型配置；默认 MCP 子进程使用宿主的 Python 环境，因此该环境也需安装这些依赖。

已有 openJiuwen DeepAgent 应用也可以通过 `ComputerUseToolkit` 接入标准工具集合：

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
        return await agent.invoke({"query": "列出正在运行的应用"})
```

## 5. 直接使用 CLI

同一个 Agent 也可以通过 CLI 直接运行。首次执行 `uv run --extra agent computer-use-agent --init-config`，在生成的 `~/.computer-use/agent/config.json` 中填写 `model`、`provider`、`api_base`、`api_key`，随后进入对话或执行单次任务：

```bash
uv run --extra agent computer-use-agent
uv run --extra agent computer-use-agent "打开预定会议表单，停在提交前"
```

命令行选项可通过 `uv run --extra agent computer-use-agent --help` 查看。

## 6. 更新源码

修改 Python 源码后，重启宿主应用或重新创建 Agent／Toolkit 会话；修改依赖或入口后，在源码仓库重新运行 `uv sync --no-dev --extra agent`，并同步宿主项目的依赖。修改原生后端后，先运行 `uv run --no-dev --extra agent computer-use-build`，再重启会话。

引擎调用与观察行为见[MCP 指南中的接口参考](mcp.md#4-接口使用参考)；开发出包见[打包说明](../PACKAGING.md)。
