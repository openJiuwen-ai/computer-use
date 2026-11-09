# MCP：从源码构建、安装与接入

[返回 README](../README_CN.md) | [English](mcp.en.md) | [DeepAgent 接入](deepagent.md)

通过 stdio MCP 为已有 Agent 增加电脑操作能力，模型配置继续由 Agent 客户端管理。

## 1. 准备环境

安装 Python 3.12 或更高版本及 `uv`。构建和 MCP 服务均在被操作桌面的电脑上运行。

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
`uv pip install '/absolute/path/to/computer_use_toolkit-0.0.1-py3-none-any.whl[mcp]'`，
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

macOS 上 App／Element 操作固定不注入全局鼠标或键盘；必要时由 Agent 自主选择 Computer 的直接输入方法。Windows／Linux 当前保持原有的全局输入能力。每次动作分别记录所选路线声明的全局输入需求与实际影响。若需显式限制某个会话，将 `--no-allow-global-input` 添加到连接器的 `args`；目标电脑自身的系统权限仍须具备。

## 4. 接口使用参考

SDK 接口是同步的：每个操作返回结果或抛出异常后，再执行下一条语句。
通过 `computer.app(id)` 选择应用，用 `app.read(view="both")` 观察界面，再用 `app.element(name_or_path)` 定位控件。
目标已经明确时可以直接链式操作，例如 `app.element("保存").click()`。

MCP 中观察自动展示：首次展示已采集的内容，后续默认显示变化（diff），包括写后观察及同一次调用中的连续操作。
每个窗口分别标明 AX、图像采集情况和具体缺口；full/diff/unchanged 表示文字呈现模式。
同次工具的连续写入保留每条回执，同范围自动观察合并为最终结果；独立弹层及显式打印/完整观察保留。
窗口坐标操作的写后观察保留截图并采集独立的 AX 信息，缺少 AX 不会使已取得的截图不可用。
需要重新获得完整上下文时调用 `app.read(full=True)`，需要指定窗口时增加 `window=`；不需要客户端接入私有历史协议。
SDK 返回的 `state.text` 始终是本次读取范围的完整文字，展示 diff 不改变元素绑定和截图坐标。
所有交给模型的截图统一使用**实际展示图片的像素坐标**：左上角 `(0, 0)`，向右为 x，向下为 y。
按图片旁提供的 `point((x, y))` 示例创建定位值；模型无需计算缩放、多屏偏移、窗口位置或弹层偏移。
桌面图使用 `p = computer.point((x, y))`，应用图使用 `p = app.point((x, y), window=ref)`；
组合图始终使用整张展示图片的坐标。应用内点击用 `app.click(at=p)`，显式全局点击用
`computer.click(target=p)`，切换输入路线时直接复用同一个 p。坐标需取自此前已经收到的图片；
如需跨后续读取复用，应先保留 p。图片来源不明或映射失效时，不提供可执行定位示例。
在 Windows MCP 的一次调用内，`computer.point()` 仍按模型已收到的桌面图取点，直到更新的图片交付。
派发时仍检查对应捕获与当前显示布局。
MCP 中打印观察、观察的 `.text` 或直接文字切片，都复用自动展示，不额外输出全文；
SDK 内切片值及文件输出仍保持原意。需要限制读取范围时使用子树或 `read_text`。

读取默认不为刷新画面而激活或提升窗口。在 macOS 和 Windows 上，调用方可显式使用
`app.read(view="both", allow_foreground=True)`，允许 SDK 在已确认的原窗口范围内准备观察；
指定窗口时使用 `app.read(window=ref, allow_foreground=True)`。
这可能改变焦点和窗口层次，但不注入全局鼠标或键盘、不自动切回其他应用。
结果的 `preparation` 和文字说明保留实际请求，图像保留本次采样来源。
观察目标尚未明确时仍返回窗口选择信息。Linux 暂不支持该准备能力。

对象写入在执行结果之外附带有界的相关观察，可读取 `result.observation`、`result.observation_status`
和 `result.observation_error`。`result.target`、`result.intent` 和 `result.status` 保留操作目标、意图及派发／验证结果。
目标失效、目标信息暂时无法确认、模态阻挡，
以及 `effect="partial"` 或 `"possible"` 的 `ComputerUseError`，在原会话仍可观察时也附带这些字段。
可直接使用错误附带的当前观察决定下一步；原错误与效果保持不变，SDK 不会重放不确定的输入。
macOS 的 Computer 全局输入及 Windows 的 Computer 按键、键入和粘贴返回
`observation_status="not_requested"`。需要确认界面效果时显式读取。

### Python SDK

将以下示例保存为 `list_apps.py`，通过 `uv run --no-dev python list_apps.py` 运行：

```python
from computer_use import Computer


with Computer() as computer:  # 仍优先选择机制合格的低干扰路线。
    for app in computer.list_apps():
        print(app.id, app.display_name, app.is_running)
```

`Computer.close()` 释放会话持有的驱动资源，上下文管理器在退出时自动调用它。
公开 SDK 方法均直接同步调用，包括 `Observation.read_details()`。
嵌入已有 asyncio 或 GUI 应用时，在工作线程中运行阻塞的电脑操作代码，使宿主保持响应；
内部驱动通信、进度和清理仍使用异步执行。

`allow_global_input` 是严格的布尔型会话选项，默认 `True`，不授予 macOS 系统权限。
macOS 上，App／Element 的整次操作（含准备、恢复、清理）固定不注入全局鼠标或键盘，
但允许必要的窗口激活和焦点准备。原来的全局鼠标后备路线改为定向投放。
确需全局输入时，由 Agent 选择 Computer 的直接输入方法；该选择只作用于本次调用。
Windows／Linux 当前保持原有行为，允许对象操作使用全局输入；相同边界的落地和验收后续实施。
`Computer(allow_global_input=False)` 会限制显式全局输入并返回 `permission_required`。
macOS 对象操作没有合格的非全局路线时返回 `input_unavailable`，不是要求重新授权对象方法。

macOS 的 Computer 直接输入使用系统全局事件，由当前桌面和键盘焦点决定接收者。
裸坐标是全局屏幕 point，可包含副屏的负坐标；不依赖此前截图，不额外激活目标应用或归还前台；全局点击引起的自然激活和焦点变化由系统处理。
这是程序化设备输入接口，不能直接填入模型看到的截图像素。已知系统屏幕坐标时，可以不先读取：

```python
computer.click(at=(640, 360))
computer.type_text("全局输入")
computer.press_key("Return")
```

看图操作统一通过 `point()` 保留展示图片上的位置，由 SDK 完成转换；已有元素也可作为鼠标定位来源：

```python
field = app.element("输入框")
field.type_text("普通输入", replace=True)       # macOS：AX／定向机制
computer.click(target=field)                  # 只定位到屏幕点，再全局点击
computer.press_key("super+a")                # 当前系统接收者中的显式全选
computer.type_text("全局输入")               # 不隐式聚焦或替换特定编辑器
computer.paste("多行\n内容")                 # 当前焦点中的全局粘贴

# 从此前实际交付的 app 截图确定位置；point() 本身不读取或操作电脑。
p = app.point((120, 80), window=ref)
app.click(at=p)                              # macOS：定向鼠标
computer.click(target=p)                     # 换算到屏幕点，再由系统路由

# 如果此前收到的是桌面截图，同样填入图片上的像素。
p = computer.point((120, 80))
computer.click(target=p)
```

`Target` 保留元素或图像点的定位来源，后续读取不会把旧点改指新图。
用于 Computer 鼠标输入时只解析一次屏幕位置，不自动处理遮挡、滚入可见或焦点；定位后不再锁定原对象。
全局拖拽两端可分别定位，不要求来自同一窗口或图片。对象方法使用 Target 时仍保持原对象及图像绑定。
全局键盘方法不接受 `target=` 或隐式 `replace=True`；需要时先显式点击、选择，再输入。
全局输入返回派发回执；需要界面结果时显式读取，读取失败不影响下一次全局输入。
鼠标位置和系统焦点保留操作产生的结果，不自动归还。实际输入影响见 `result.impact`。
在 Windows 上，`computer.press_key(key)`、`computer.type_text(text)` 和
`computer.paste(text)` 直接交给系统当前接收者，无须先截图或获取无障碍树。
调用前按需要显式准备焦点。鼠标操作仍使用已有的截图坐标映射。
Windows 粘贴将本次文本留在系统剪贴板，直到后续写入覆盖。派发回执只说明粘贴按键已发送。
如果还需粘贴下一段，先观察目标并确认上一次粘贴已生效，避免覆盖慢应用尚未读取的内容。
Linux 保留原有桌面截图上下文，粘贴使用 App 或 Element 方法。

`App` 表示应用范围，`list_apps()` 始终列出应用。App 和桌面观察发布窗口编号。
通过 `app.read(window=ref)` 观察指定窗口；`element`、`point`、`click`、`scroll`、`drag`、
`type_text`、`paste` 和 `press_key` 同样接受可选 `window=`。窗口选择和普通读取不激活窗口。
窗口编号在桌面、App 和写后观察间共用。窗口绑定以原生身份为准。

Windows 上，如果任务给出了完整窗口标题，可使用 `app = computer.app(window_title=title)`。
第一次 `app.read()` 从当前桌面的可见原生顶层窗口中按区分大小写的标题匹配并绑定唯一窗口。
多个匹配返回 `ambiguous_target`，目录缺口导致无法确认唯一匹配时返回 `observation_incomplete`。
标题变化后继续使用同一 App，原窗口关闭后句柄失效。

该 App 的读取、`read_details()` 和写后观察只覆盖已绑定窗口。普通 `app.read(window=ref)`
也可能包含归属已确认的窗口。标题绑定的 App 使用 `read(menu_path=...)` 会返回 `invalid_request`。
菜单路径由支持该能力的平台通过普通 App 范围提供。

```python
app = computer.app("com.apple.TextEdit")
view = app.read(view="both")
# a、b 使用观察中提供的两个窗口编号。
app.read(window=a, view="both")
app.read(window=b, view="both")
app.click(at=(200, 150), window=a)  # 复用 A 实际交付的图像，无须重读来切换范围。
app.element("保存", window=b).click()
app.press_key("Return", window=a)
```

公开对象只有 `Computer`、`App`、`Element`，没有公开 `Window` 或窗口工厂。
macOS 和 Windows 可通过 `app.close_window(ref)` 正常请求关闭指定窗口。macOS 使用窗口声明的关闭控件，
即使共享标识遮住标题栏按钮也可操作。Windows 使用该窗口提供的 UI Automation `Window.Close` 动作。
两条路线复用已有输入和写后观察，由调用方根据观察处理保存确认。
应用可拒绝关闭或弹出确认框；回执的 `intent` 为 `closeWindow`，`dispatched` 表示已派发请求，
`window_lifecycle` 与附带观察说明已取得的窗口状态。Linux 的该能力后续实现。

标题绑定的 App 调用 `close_window` 后，附带观察只覆盖原窗口，`choices` 为空。
只有任务范围包含独立的确认窗口时，才在该窗口范围内另行观察。回执中的 `window_lifecycle`
为 `closed` 时可确认窗口已关闭。

同一应用的元素编号跨读取范围保持一致；`app.element(ref)` 保持原控件绑定，读取其他窗口不改绑它。
数字元素引用附带 `window=` 时检查其范围是否相符。指定窗口的名称查询在本次调用内准备定位事实，
不要求先公开读取该窗口。省略 `window=` 的名称查询和键盘输入使用最近实际交付的明确上下文，
不会因为执行时系统焦点变化而改选目标。

每个窗口独立保留当前已交付图像；组合图仍使用整张已展示图片的坐标，由 SDK 映射实际接收窗口。
未交付采样不替换已交付映射。窗口关闭后旧窗口编号和图像失效，应用仍可发现其他窗口。
`choices` 中的每项是 `WindowInfo`，包含可传给 `window=` 的 `id` 和描述性 `title`。
写后观察可以展示新发现的窗口，不改绑已有 Element 或图像目标。窗口操作由 SDK 准备接收链，
确有歧义时返回具体候选。macOS 已纳入真实验收；其他平台沿用共同接口，真实多窗口验收为后续工作。

macOS 的存续元素引用不因无关布局包装变化、局部观察或其他引用的缓存压力而失效；必要的操作条件由 SDK 更新。
元素真正结束、窗口关闭后重开或真实文档归属改变时，旧引用仍会拒绝输入，并附带可取得的相关观察。
`app.element(ref)` 可取回本应用已经发布的引用；构造句柄不声明目标仍存续或已获操作资格。
实际读取和写入均检查原窗口范围与生命周期，失败写入可在同一次调用中返回当前观察。
操作身份与历史文字缓存独立：大内容缓存可以回收，最小引用元数据保留至明确退休或会话结束。
本轮原生引用稳定性改进及真实 SDK／MCP 验收限于 macOS；Windows／Linux 的对应原生实现与验收列为后续工作。

普通 SDK 返回原始截图及对应像素坐标。MCP 交付缩图时，公开观察的文字与 PNG 成对使用交付图坐标，
文字切行或拼接仍保持相同尺度；图像定位值按实际编码尺寸逐轴映射回原图。
任意 PIL 裁图不自动获得输入映射。macOS 的 Computer 裸坐标始终是屏幕 point，图片点请用 `point()` 明确转换。

`read_details()` 中的原始 native／AX 详情保留其声明的屏幕／AX 坐标系，不能直接当作截图像素。

执行和观察分别交付结果。`status` 表示派发或已有的局部验证；模型依据实际界面判断业务任务完成情况。
`observation_status` 的值为 `available`、`partial`、`unavailable` 或 `not_requested`。
`window_lifecycle` 的值为 `observed`、`closed`、`unknown` 或 `not_applicable`，来自窗口生命周期证据。
原窗口结束时，相关观察仍可给出当前窗口选择，旧窗口编号和 Element 不会改投这些候选。
`observation_error.session_usable` 表示原会话是否仍可使用。

MCP 与参考 Agent 共用静态工具说明。工具返回展示界面内容、对象归属、执行进度和具体缺口；
每张图携带编号、来源、尺寸和坐标接收对象，DeepAgent 转换后仍保留对应说明。
`ComputerUseError` 和 `ObservationError` 的返回字段已移除指导性的 `next`；`TextPage.next` 继续提供分页游标。

`ActionResult.impact` 和 `ComputerUseError.impact` 提供只读的执行证据。
`global_input_allowed` 记录会话授权；每个 `attempts` 项分别保留路线声明的 `declared_global_input`、
`preparation_potential`，以及派发、清理和 `actual` 原生证据。
`actual.effects` 按阶段、设备、传输、机制和调用状态分组，不是逐按键日志，也不证明应用已消费输入。
`foreground_changed` 和 `clipboard_restoration` 保留已知或未知的实际结果。
`impact`／`actual` 缺失或 `actual.coverage` 不完整表示证据不足，不表示没有影响。
即使 coverage 完整，其范围也仅为该次原生执行步骤，不包含独立读取、稍后的会话清理或应用自身的异步后果。

单次 SDK 操作以约 1000ms 为性能目标，默认不设置总完成截止。原生请求保留自身超时，
显式提供的操作截止由嵌套定位、准备、原生请求与观察共用，不为恢复重新计时。
已返回的原生执行事实先保存，再处理后续观察或清理。日志中的 `sdk_operation_end` 记录实际耗时、
`targetExceeded`、超过总预算的 `overrun`、观察完整性和具体缺口；MCP 元数据另记录整次工具调用时间，包含启动、多个 SDK 操作及最终交付。
一次工具脚本可连续调用多个 SDK 方法，逐操作时间与工具总时间分别核对。

macOS 的写后观察会利用已建立的应用变更通知，在原预算内补采新窗口或欠缺组件；纯截图观察不会因此初始化 AX。
固定窗口独立保留实际交付截图的坐标映射，隐藏／重显与真正销毁分别处理。
这些通知恢复及本轮原生输入、截图性能改进先实现 macOS；Windows／Linux 的对应能力及实机验收列为后续工作。

## 5. 开发出包

只使用 Python SDK 时可通过 `uv sync --no-dev` 安装。开发测试使用普通 `uv sync`，会安装测试、检查与构建工具。开发包与后端制品的构建流程见[打包说明](../PACKAGING.md)。
