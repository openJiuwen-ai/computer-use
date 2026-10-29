<div align="center">

# Computer Use

**openJiuwen 的计算机操作引擎与智能体：让智能体像人一样读屏幕、点控件、填表单**

[English](README.md) | 简体中文

<a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-green.svg" alt="License" /></a>
<img src="https://img.shields.io/badge/python-3.12%2B-blue.svg" alt="Python" />
<img src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey.svg" alt="Platform" />

</div>

## 简介

Computer Use 是 openJiuwen 智能体系统服务层（Agent System Service）中的桌面操作组件。它把"操作一台计算机"封装成一个可独立部署的执行运行时：读取原生应用的无障碍树与窗口截图，按元素或坐标派发鼠标键盘输入，并对每一步给出可核验的回执。任何智能体框架都可以通过 MCP、Python SDK 接入它；openJiuwen 的 JiuwenSwarm 以子代理方式使用它。当前支持 macOS、Windows、Linux 多操作系统。

开发者有两种形式可以接入使用：

**方法 1：仅使用后端 computer_use 引擎。** 与"一个动作一个工具"的做法不同，本引擎只向模型暴露一个工具 `computer_use(title, code)`：模型编写 Python 代码，调用 `Computer → App → Element` 三级 SDK 完成多步操作。无障碍树优先、截图其次；动作只返回 `dispatched` 或 `verified` 回执，结果必须通过重新读取界面来确认。

**方法 2：使用接入 computer_use 引擎的 DeepAgent。** 用户直接发送问题给 agent，由 agent 识别、观测和操控桌面完成任务。

## 快速开始

### 安装与准备宿主

安装 Python 3.12 或更高版本及 `uv`，然后克隆仓库并安装依赖：

```bash
git clone https://gitcode.com/openJiuwen/computer-use.git
cd computer-use
uv sync --group dev
```

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

构建或准备原生后端，并检查桌面访问条件：

```bash
uv run computer-use-build
uv run computer-use-permissions
```

macOS 可运行 `uv run computer-use-permissions --request` 请求缺失权限，在**系统设置 → 隐私与安全**中批准后重新检查。请使用与启动 MCP 相同的终端或宿主连接执行这些命令。

### 启动 MCP 服务

```bash
uv run computer-use-mcp --desktop exclusive
```

服务使用 stdio 通信。对于采用 `mcpServers` 配置的客户端，可使用：

```json
{
  "mcpServers": {
    "computer-use": {
      "command": "uv",
      "args": [
        "--directory", "/absolute/path/to/computer-use",
        "run", "computer-use-mcp", "--desktop", "exclusive"
      ]
    }
  }
}
```

将仓库路径替换为目标电脑上的绝对路径。`exclusive` 允许必要的前台准备和整桌面输入；`shared` 将输入限制在选定的 App 或 Window 范围内。不显式指定时，读取 `COMPUTER_USE_DESKTOP`，默认值为 `shared`。

`computer_use` 工具中已提供 `computer`、`print()` 和 `print_image()`。可以先查询应用引用：

```python
print(await computer.list_apps())
```

通过 `computer.app(id)` 选择应用，用 `await app.read(view="both")` 观察界面，再用 `await app.element(name_or_path)` 定位控件后执行操作。

### 使用 Python SDK

将以下示例保存为 `list_apps.py`，通过 `uv run python list_apps.py` 运行：

```python
import asyncio

from computer_use import Computer


async def main():
    computer = Computer()
    for app in await computer.list_apps():
        print(app.id, app.display_name, app.is_running)


asyncio.run(main())
```

### 使用 Computer Use Agent

使用 Python 3.12 或 3.13 安装 Agent 可选依赖并生成模型配置：

```bash
uv sync --extra agent
uv run computer-use-agent --init-config
```

在 `~/.computer-use/agent/config.json` 中填写 `model`、`provider`、`api_base` 和 `api_key`。模型需要支持工具调用和图片输入。环境变量 `MODEL_NAME`、`MODEL_PROVIDER`、`API_BASE`、`API_KEY` 覆盖文件配置；也支持 `OPENAI_MODEL`、`OPENAI_BASE_URL` 和 `OPENAI_API_KEY`。

```bash
uv run computer-use-agent
uv run computer-use-agent "打开预定会议表单，停在提交前"
```

省略任务时进入多轮对话。界面流式显示回复和操作标题；回车发送，Ctrl+J 或 Alt+回车换行。`/debug`、`/normal` 切换工具详情；`/compact` 压缩历史并保留 Python 会话；`/new` 开始新对话；`/exit` 或 Ctrl+D 退出，Ctrl+C 中断并退出。macOS 终端通过本机预览展示收到的截图，`--no-preview` 可关闭。

本机 Agent 默认使用 `exclusive`，`--desktop shared` 选择共享模式，`--config` 指定配置文件。可选的 `mcp` 对象通过 `command`、`args`、`env`、`cwd` 连接其他桌面宿主，例如通过 SSH 在目标电脑启动 `computer-use-mcp --desktop exclusive`；使用自定义 MCP 命令时，桌面模式由该命令决定。对话记录与截图保存在 `~/.computer-use/agent/runs/`。

### 接入 openJiuwen DeepAgent

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

已有 DeepAgent 可通过 `ComputerUseToolkit` 接入原生工具集合：

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

## 架构简介

`computer_use_agent/` 组装 openJiuwen DeepAgent 及电脑操作工具，`interface_adapter/cli/` 提供直接对话界面。标准 Agent 调用和对话界面共用现有 MCP 执行环境。

MCP 宿主通过持久化 Python 执行环境向智能体提供 SDK。App 和 Window 共用 `UI` 接口，Element 保留独立的操作目标：

```text
智能体 / MCP 客户端
        ↓ stdio
interface_adapter/mcp
        ↓ Python SDK
Computer → App / Window → Element
        ↓ 读取 / 查询 / 写入管线
os_adapter：macOS / Windows / Linux
        ↓ 原生无障碍、截图与输入
桌面应用
```

公共 Python 层管理句柄、目标范围、观察结果展示和操作选路。各系统后端负责解析操作系统对象、准备输入接收者并派发选定操作。操作回执说明派发或验证状态，智能体通过再次读取界面确认任务效果。

## 功能特性

- **单工具代码执行**：模型写 Python，变量跨调用保留，一次调用完成多步工作流。
- **无障碍树优先**：元素按十进制 ref 编号，支持 `element(path)` 路径查询、文本分页读取、菜单读取。
- **可核验回执**：每个动作返回 `dispatched` / `verified`，失败结构化为 `code / effect / next`。
- **共享与独占两种桌面模式**：默认不抢前台，对后台窗口定向输入；可按需启用前台辅助。
- **跨平台原生驱动**：macOS、Windows、Linux 各一套，协议一致。
- **可观测**：每次执行写入 `~/.computer-use/logs` 的 JSONL 日志，含代码、观测与回执。

## 项目结构

核心组件的目录组织如下：

```
computer-use/
├── interface_adapter/       # Agent 与用户接入
│   ├── mcp/                 # stdio MCP 宿主与持久化 Python 执行环境
│   └── cli/                 # 直接对话、配置与本机预览
├── computer_use_agent/      # openJiuwen DeepAgent 组装与工具接入
├── computer_use_engine/     # computer_use SDK、观察与操作管线
├── os_adapter/              # 各平台原生后端与共用资源
│   ├── macos/
│   ├── windows/
│   └── linux/
├── scripts/                 # 原生构建、权限检查与资源生成
├── tests/                   # 单元测试
├── pyproject.toml           # 包元数据、依赖与命令行入口
├── LICENSE                  # Apache License 2.0
└── OPEN_SOURCE_SOFTWARE_NOTICE.md
```

## 完整文档

- [SDK 接口参考](computer_use_engine/__init__.pyi)：`Computer`、`App`、`Window`、`Element` 的公开接口与参数类型。
- [MCP 服务入口](interface_adapter/mcp/server.py)：工具定义、执行说明与桌面模式参数。
- [原生环境准备命令](scripts/native.py)：各平台构建、权限检查与 Linux 会话配置。
- [DeepAgent 接入](computer_use_agent/agent.py)：标准 Agent 入口与会话生命周期。
- [直接对话](interface_adapter/cli/main.py)：命令行选项与交互命令。

## 参与贡献

欢迎通过 Issue 与 Pull Request 参与。提交前请确保：

1. 原生驱动改动附带可在本机编译通过的说明；
2. 引擎层改动不引入对任何智能体框架的依赖；
3. 新增行为有对应的单元测试或可复现的探针脚本。

## 开源许可证

本项目基于 [Apache License 2.0](LICENSE) 开源。

本产品仅作为流程编排工具，不包含 AI 模型能力；用户在连接 AI 模型用于特定业务场景时，需自行承担欧盟 AI 法案等相关合规义务。
