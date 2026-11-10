# MCP：从源码构建、安装与接入

[返回 README](../README_CN.md) | [English](mcp.en.md) | [DeepAgent 接入](deepagent.md)

通过 stdio MCP 为已有 Agent 增加电脑操作能力，模型配置继续由 Agent 客户端管理。

## 1. 准备环境

安装 Python 3.11 或更高版本及 `uv`。构建和 MCP 服务均在被操作桌面的电脑上运行。

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
uv sync --no-dev --extra mcp
```

SDK 和 MCP 服务以 editable 方式安装到仓库的 `.venv`，不安装模型依赖。以下命令均在仓库根目录执行。

构建或准备原生后端，并检查桌面访问条件：

```bash
uv run --no-dev --extra mcp computer-use-build
uv run --no-dev --extra mcp computer-use-permissions
```

macOS 可运行 `uv run --no-dev --extra mcp computer-use-permissions --request` 请求缺失权限，在**系统设置 → 隐私与安全**中批准后重新检查。请使用与启动 SDK 或其 MCP 接入层相同的终端或宿主连接执行这些命令。

## 3. 接入自己的 Agent

完成上述源码安装、平台依赖准备和后端构建后，即可把 MCP 服务接入自己的 Agent。
服务在被操作桌面的电脑上运行，使用独立的 `.venv`，由 Agent 客户端通过 stdio 启动和调用。
Agent 需支持 MCP 工具调用；要使用截图，还需客户端和模型支持图片输入。

**1. 配置连接器。** 在 Agent 的 MCP 配置中添加 `computer-use`。
对于采用 `mcpServers` 格式的客户端，macOS / Linux 可使用：

```json
{
  "mcpServers": {
    "computer-use": {
      "command": "/absolute/path/to/computer-use/.venv/bin/computer-use-mcp",
      "args": [],
      "env": {
        "PYTHONSAFEPATH": "1"
      }
    }
  }
}
```

将 `command` 替换为本机源码仓库中启动程序的绝对路径。
Windows 对应路径为 `C:/absolute/path/to/computer-use/.venv/Scripts/computer-use-mcp.exe`。
`PYTHONSAFEPATH=1` 用于避免宿主工作目录中的同名文件夹遮蔽 SDK 包。

如果客户端提供连接器表单（例如 WorkSwarm），选择 **stdio** 类型，名称填写 `computer-use`，
命令填写上述可执行文件路径，参数留空，并添加环境变量 `PYTHONSAFEPATH=1`。
其配置存储位置和启用方式由客户端管理。

需要手动启动服务调试时，也可在仓库根目录运行：

```bash
uv run --no-dev --extra mcp computer-use-mcp
```

通过 wheel 安装时，为同一个 wheel 选择 `mcp` extra，例如
`uv pip install '/absolute/path/to/<wheel-filename>.whl[mcp]'`，
然后在该环境运行 `computer-use-mcp`。

**2. 启用并验证连接。** 保存配置后，启用连接器并重新连接 MCP；必要时重启 Agent 客户端。
确认客户端能发现 `computer_use` 和 `computer_use_reset` 两个工具，然后向 Agent 发送：

```text
请使用 computer_use 列出正在运行的应用，确认能够调用本机后端。
```

工具中已提供 `computer`、`print()` 和 `print_image()`，对应的首次验证代码为：

```python
print(computer.list_apps())
```

应看到后端返回的应用列表。工具发现确认 MCP 连通，应用枚举确认 Agent 能实际调用本机后端。
随后可选择一个有窗口的应用，执行下文的 `app.read(view="both")`，核对界面文字与截图，
并按前述说明授予所需平台权限。`computer_use_reset` 可用于清空当前 Python 执行会话。

**3. 更新源码后重新加载。** 修改 Python 源码后，重启 MCP 连接以加载新代码；
修改依赖或启动入口后，先重新运行 `uv sync --no-dev --extra mcp`。
修改原生后端后，先运行 `uv run --no-dev --extra mcp computer-use-build`，再重启 MCP 连接。

macOS 的 App 和 Element 操作使用对象或定向输入。需要全局输入时，显式使用 Computer 的直接输入方法。
在连接器的 `args` 加上 `--no-allow-global-input` 可限制全局输入。系统权限仍需在目标电脑授权。

## 4. 接口使用参考

SDK 方法同步返回结果。通过 `computer.app(id)` 选应用，用 `app.read(view="both")` 观察，再用
`app.element(name_or_path).click()` 等方法操作。显式读取每次重新采集。MCP 首次交付完整观察，
后续默认返回 diff，可用 `app.read(full=True)` 请求全文。SDK 的 `observation.text` 始终是完整文字。

同次工具调用保留每条动作回执。同一范围的自动观察可合并，独立弹层和显式读取仍分别交付。
对象写入可附带相关文字观察，查看 `result.observation`、`observation_status` 和
`observation_error`。观察失败不抹掉动作回执。需要截图时显式读取，派发回执由调用方结合界面判断。

### Python SDK

将以下示例保存为 `list_apps.py`，通过 `uv run --no-dev python list_apps.py` 运行。

```python
from computer_use import Computer


with Computer() as computer:
    for app in computer.list_apps():
        print(app.id, app.display_name, app.is_running)
```

上下文管理器退出时调用 `Computer.close()`。嵌入 asyncio 或 GUI 应用时，在工作线程中运行阻塞的 SDK 操作。

**窗口范围。** 公开对象为 `Computer → App → Element`。用观察结果中 `choices` 提供的窗口编号调用
`app.read(window=ref)`，也可向 `element`、`point`、`click` 等 App 方法传入 `window=ref`。
已有元素和图像定位值保留原绑定。名称和路径可复用最近交付的观察，使用 `app.element(name, fresh=True)`
明确现场查询。

macOS 可用 `computer.list_windows(app_name=display_name)` 按准确显示名查找同名实例的窗口。
从 `catalogue.windows` 选择后调用 `computer.app(row.app_id).read(window=row.window_ref)`。
Windows 可用 `computer.app(window_title=title)` 绑定完整标题对应的唯一窗口。
macOS 和 Windows 支持 `app.close_window(ref)`，保存确认由调用方处理。

**截图坐标。** 使用实际展示图片的像素，原点在左上角。按图片旁的示例创建定位值，由 SDK 换算缩放和位置。

```python
p = app.point((120, 80), window=ref)  # 取自已收到的应用截图。
app.click(at=p)
computer.click(target=p)             # 显式全局点击同一位置。

p = computer.point((120, 80))         # 取自已收到的桌面截图。
computer.click(target=p)
```

macOS 的 App 单左键坐标点击精确命中已展开菜单的可选项时，可以使用该项的 Press。
需要实际鼠标事件时用 `app.click(at=p, force_pointer=True)`。

**前台与全局输入。** macOS 和 Windows 的普通读取使用窗口当前状态。可显式传入
`app.read(window=ref, allow_foreground=True)` 允许为观察准备目标窗口，结果包含 `preparation`。
macOS 的 Computer 直接输入使用系统当前桌面和键盘焦点。裸坐标是系统屏幕 point，可以包含副屏负坐标。
显式全局输入可以直接派发，需核对界面时另行读取。会话的 `Computer(allow_global_input=False)` 会限制全局输入。

完整签名见 [SDK API](../computer_use_engine/__init__.pyi)。只安装 SDK 可用 `uv sync --no-dev`。
开发出包见[打包说明](../PACKAGING.md)。
