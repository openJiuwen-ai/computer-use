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

## 架构简介

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
├── interface_adapter/mcp/   # stdio MCP 宿主与持久化 Python 执行环境
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

## 参与贡献

欢迎通过 Issue 与 Pull Request 参与。提交前请确保：

1. 原生驱动改动附带可在本机编译通过的说明；
2. 引擎层改动不引入对任何智能体框架的依赖；
3. 新增行为有对应的单元测试或可复现的探针脚本。

## 开源许可证

本项目基于 [Apache License 2.0](LICENSE) 开源。

本产品仅作为流程编排工具，不包含 AI 模型能力；用户在连接 AI 模型用于特定业务场景时，需自行承担欧盟 AI 法案等相关合规义务。
