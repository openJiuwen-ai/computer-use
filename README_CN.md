<div align="center">

# Computer Use

**openJiuwen 的计算机操作引擎与智能体：让智能体像人一样读屏幕、点控件、填表单**

[English](README.md) | 简体中文

<a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-green.svg" alt="License" /></a>
<img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python" />
<img src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey.svg" alt="Platform" />

</div>

## 简介

Computer Use 是 openJiuwen 智能体系统服务层（Agent System Service）中的桌面操作组件。它把"操作一台计算机"封装成一个可独立部署的执行运行时：读取原生应用的无障碍树与窗口截图，按元素或坐标派发鼠标键盘输入，并对每一步给出可核验的回执。任何智能体框架都可以通过 MCP、Python SDK 接入它；openJiuwen 的 JiuwenSwarm 以子代理方式使用它。当前支持 macOS、Windows、Linux 多操作系统。

开发者有两种形式可以接入使用：

**方法 1：仅使用后端 computer_use 引擎。** 与"一个动作一个工具"的做法不同，本引擎只向模型暴露一个工具 `computer_use(title, code)`：模型编写 Python 代码，调用 `Computer → App → Element` 三级 SDK 完成多步操作。无障碍树优先、截图其次；动作只返回 `dispatched` 或 `verified` 回执，结果必须通过重新读取界面来确认。

**方法 2：使用接入 computer_use 引擎的 DeepAgent。** 用户直接发送问题给 agent，由 agent 识别、观测和操控桌面完成任务。

## 快速开始

待补充。

## 架构简介

待补充。

## 功能特性

- **单工具代码执行**：模型写 Python，变量跨调用保留，一次调用完成多步工作流。
- **无障碍树优先**：元素按十进制 ref 编号，支持 `find` 路径查询、文本分页读取、菜单读取。
- **可核验回执**：每个动作返回 `dispatched` / `verified`，失败结构化为 `code / effect / next`。
- **共享与独占两种桌面模式**：默认不抢前台，对后台窗口定向输入；可按需启用前台辅助。
- **跨平台原生驱动**：macOS、Windows、Linux 各一套，协议一致。
- **可观测**：每次执行写入 `~/.computer-use/logs` 的 JSONL 日志，含代码、观测与回执。

## 项目结构

项目主要分为以下四层（自上而下）：

```
computer-use/
├── interface_adapter/       # 系统服务接入层：JiuwenSwarm rail 原生接入；A2A / MCP / CLI 三方接入
├── computer_use_agent/      # agent loop 组装层：基于 agent-core 开发的 DeepAgent，
│                            #   包括上下文管理、观测、决策、级联定位、多路径择优、原子编译
├── computer_use_engine/     # 引擎层：将各操作系统的原始接口转换成 Agent 可读的通用接口，
│                            #   增强驱动，可支持 MCP 接入 Codex 等外部智能体
├── os_adapter/              # 操作系统适配层：macOS、Linux、Windows 等操作系统原始接口
│   ├── macos/
│   ├── windows/
│   └── linux/
├── tests/                   # 单元测试
├── docs/                    # 文档
├── LICENSE                  # Apache License 2.0
└── OPEN_SOURCE_SOFTWARE_NOTICE.md
```

## 完整文档

待补充。

## 参与贡献

欢迎通过 Issue 与 Pull Request 参与。提交前请确保：

1. 原生驱动改动附带可在本机编译通过的说明；
2. 引擎层改动不引入对任何智能体框架的依赖；
3. 新增行为有对应的单元测试或可复现的探针脚本。

## 开源许可证

本项目基于 [Apache License 2.0](LICENSE) 开源。

本产品仅作为流程编排工具，不包含 AI 模型能力；用户在连接 AI 模型用于特定业务场景时，需自行承担欧盟 AI 法案等相关合规义务。
