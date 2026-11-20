# 回归测试

`tests/regression/` 包含原生协议回放、读写行为矩阵、MCP 和 Agent 交付、平台模拟与打包测试。测试代码、夹具和 SDK 一起维护。适用于同一个契约的快速 UT 仍放在 `tests/unit_tests/`。

## 运行

从仓库根目录执行。直接运行 `pytest` 或显式指定 `tests/unit_tests` 均只收集 UT。回归任务显式传入自己的收集范围。

```sh
uv sync --no-default-groups --group test --group build --extra agent
uv run --no-sync pytest tests/unit_tests
uv run --no-sync pytest tests/regression --html=outputs/regression_report.html
uv run --no-sync pytest tests/unit_tests tests/regression --html=outputs/regression_report.html
```

完整回归需要同一源码、主机平台和 Python ABI 的 Rust 读扩展。先按[源码构建指南](../../docs/build-from-source.md)准备 Rust 工具链，再从当前检出生成扩展。如果需要跑本机驱动握手测试，也构建当前检出的驱动。

```sh
uv run --no-sync python -c "from pathlib import Path; from scripts.read_engine_build import build_read_engine, host_build; root = Path.cwd(); build_read_engine(root, root / 'computer_use_engine/interaction/read/accessibility' / host_build().filename)"
uv run --no-sync computer-use-build
```

全量报告需记录跳过的用例。Windows 文件系统、GDI+ 和公共数据目录用例需要 Windows。真实桌面验收及原生测试继续使用各自的平台入口。
