# DeepAgent：从源码构建、安装与接入

[返回 README](../README_CN.md) | [English](deepagent.en.md) | [MCP 接入](mcp.md)

将 Computer Use DeepAgent 集成到应用中，或为已有 openJiuwen DeepAgent 增加电脑操作工具。

## 1. 准备环境

按[源码构建指南](build-from-source.md)准备 Rust、系统工具链和桌面权限。DeepAgent 使用 Python 3.11–3.13；下面的命令选择 3.12。

## 2. 安装源码并构建后端

```bash
git clone https://gitcode.com/openJiuwen/computer-use.git
cd computer-use
uv sync --python 3.12 --no-dev --extra agent
```

SDK、MCP 和 Agent 代码以 editable 方式安装到仓库的 `.venv`，同时安装 openJiuwen 等 Agent 依赖。此命令选择 Python 3.12。已有 Python 3.11 或 3.13 环境也可选择对应版本。以下命令均在仓库根目录执行。

构建或准备原生后端，并检查桌面访问条件：

```bash
uv run --python 3.12 --no-sync computer-use-build
uv run --python 3.12 --no-sync computer-use-permissions
```

macOS 可运行 `uv run --python 3.12 --no-sync computer-use-permissions --request` 请求缺失权限，在**系统设置 → 隐私与安全**中批准后重新检查。请使用与启动 SDK 或其 MCP 接入层相同的终端或宿主连接执行这些命令。

## 3. 创建电脑操作 Agent

通过 wheel 安装时选择 `agent` extra，其中包含 MCP，并要求 `openjiuwen>=0.1.19.post3`。

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
uv run --python 3.12 --no-sync python desktop_agent.py
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

同一个 Agent 也可以通过 CLI 直接运行。首次执行 `uv run --python 3.12 --no-sync computer-use-agent --init-config`，在生成的 `~/.computer-use/agent/config.json` 中填写 `model`、`provider`、`api_base`、`api_key`，随后进入对话或执行单次任务：

```bash
uv run --python 3.12 --no-sync computer-use-agent
uv run --python 3.12 --no-sync computer-use-agent "打开预定会议表单，停在提交前"
```

命令行选项可通过 `uv run --python 3.12 --no-sync computer-use-agent --help` 查看。

模型历史按观察范围保留截图。新截图与同一范围的现有截图完全相同时，引用已有图片；截图像素和坐标保持原样。
文字历史只折叠完全相同的完整树，保留不同的完整树、diff 和操作回执。

Agent 在第一次请求模型前调用一次应用目录接口，确认 MCP 和原生后端可用。纯路由或无桌面场景可用
`--no-desktop-check`，也可以设置 `AgentConfig(check_desktop=False)`。

用 `--pause-on-user-input` 或 `AgentConfig(pause_on_user_input=True)` 可在检测到近期键鼠活动时暂停派发。
桌面空闲后，Agent 会收到重新观察的提示，原调用不会派发。最多等待 60 秒。暂停检查作用于两次桌面调用之间，
依赖 Agent 所在桌面会话提供的空闲时间。分别使用
[macOS Core Graphics](https://developer.apple.com/documentation/coregraphics/cgeventsourcestateid)、
[Windows GetLastInputInfo](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlastinputinfo)
和 [X11 Screen Saver Extension](https://www.x.org/releases/X11R7.7/doc/scrnsaverproto/saver.pdf)。

## 6. 更新源码

按[源码构建指南](build-from-source.md#3-验证与更新)更新 Python、Rust 或驱动。若宿主项目通过独立的环境引用本仓库，也需在该环境更新安装，再重启应用或重新创建 Agent／Toolkit 会话。

引擎调用与观察行为见[MCP 指南中的接口参考](mcp.md#4-接口使用参考)；开发出包见[打包说明](../PACKAGING.md)。
