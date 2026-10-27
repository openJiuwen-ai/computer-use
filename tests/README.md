# 核心 SDK 测试

```bash
uv sync --all-extras --group dev
uv run pytest
```

HTML 测试报告生成到 `outputs/unit_test_report.html`。

- `sdk/`：SDK、Pipeline、观察、输入、协议与冻结行为回归。
- `integrations/mcp/`：真实 stdio MCP、持久执行、观察交付和取消。
- `integration/`：原生端口集成与发行包资源。
- `backends/`：平台机制、构建支持及进程辅助。
- `native/`：macOS、Windows、Linux 驱动的机制测试和桌面验收。
- `checks/`：文档和架构约束检查。
- `fixtures/`、`baselines/`：核心用例及历史行为数据。

macOS 的原生用例由 `uv run python tests/run_macos.py --native-only` 构建并运行。实际桌面验收需另行确认目标界面效果和桌面模式。
