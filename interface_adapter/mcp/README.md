# MCP 接入层导览

MCP 接入层将宿主发来的 Python 代码交给独立 worker 执行，并管理输出、取消和进程退出。它不实现控件查找、输入选路或原生动作，这些由 `computer_use` SDK 与平台驱动负责。

## 进程关系

```text
宿主 / MCP 客户端
  └─ MCP 服务进程：server → ExecutionSession
       └─ 持久 worker 进程：runtime.Runtime
            ├─ 模型代码的 Python 命名空间
            └─ computer_use SDK（computer_use_engine/，同进程导入）
                 └─ 平台驱动进程
```

worker 是执行进程，engine 是它导入的库，不是 worker 旁边的另一个服务。Runtime 负责执行代码与交付结果，桌面操作由 SDK 完成。worker 的进程隔离保护协议与生命周期，不构成权限沙箱。

模型代码使用普通同步 Python，SDK 操作直接返回结果；例如 `app.element("保存").click()`，无需 `await`。
每条操作完成或抛出异常后再执行下一条语句。SDK 的会话执行器负责衔接内部异步管线，
Runtime 的异步通信、进度、取消和清理继续运行。调用方仍以动作回执和实际交付的观察判断效果。

观察默认使用本会话已经交付的基线输出 diff，不要求宿主声明私有历史协议。
首次观察某个范围或原生对象已替换时给出完整内容；`read(full=True)` 显式请求完整展示。
普通读取与写后观察共用此规则。同次代码逐项保留动作回执，同一范围被后续状态覆盖的自动观察只交付最终结果；不同窗口/弹层、显式打印和 `full=True` 的观察仍保留。
只有实际交付的组件推进基线与图像绑定。普通异常/取消保留已捕获的待交付结果；若 worker 被强杀，尚未渲染的观察只保留在诊断日志，返回已流式保存的动作回执和 stdout。
`tree` 与 `both` 共享文字基线，添加截图不要求重复输出整棵树；截图仍按当前读取实际交付。
基线按每棵原生窗口／菜单树保存，App 与 Window 的观察组合变化不使已见窗口重新变成首次观察。
同份结果可以包含旧窗口的 diff、新窗口的完整树及无法读取的弹窗说明；不按元素名称或图片相似度混用基线。
同一窗口内的相关根节点、关系归属和展示格式变化进入差分；局部展示失败只降级对应分支，保留其原生字段。
差分基线保留节点字段的结构化签名、包含关系、原生身份和引用，不重复缓存原始采集与渲染材料。
内存仍限制为 4 MiB／32 个范围，超出时将基线转存到本 worker 的临时目录，回到已见窗口仍可比较；
容量限制不再把已见范围当成首次观察。源失效、reset 和正常关闭会回收对应基线与临时文件。
`observation_delivery_plan` 诊断记录 full／diff 选择原因与各树变化数量，便于从日志定位意外全文输出。
SDK 的 `state.text` 始终保留完整内容。宿主上下文丢失后应请求 `read(full=True)`；
`computer_use_reset` 会结束旧 worker 并清空基线，下次读取重新建立完整视图。
模型侧 `print(obs)`、`print(obs.text)` 及直接的 text 切片复用该观察的自动展示，
不会再输出一份全文或把切片当成完整基线；打印已交付或已被新观察取代的旧对象只给保留快照提示。
SDK 内字符串值、切片和文件输出仍保持原意。显式转换为普通字符串或其他字符串加工属于普通 Python 输出，
不按文字内容猜测、去重或更新观察基线。限制观察范围应使用子树／`read_text`，完整展示使用 `read(full=True)`。

## 文件按职责归类

| 类别 | 文件 | 职责与状态归属 |
| --- | --- | --- |
| MCP 入口 | `__init__.py` | 包标识；无运行状态。 |
| MCP 入口 | `__main__.py` | `python -m computer_use_mcp` 的入口，转到 server.cli。 |
| MCP 入口 | `server.py` | 注册工具、校验参数、协商能力、发送 MCP 通知；服务进程创建 ExecutionSession。 |
| 执行生命周期 | `session.py` | 服务进程监督 worker；持有在途执行、45 秒无进展看门狗、reset/退出屏障与 Journal。 |
| 执行生命周期 | `runtime.py` | worker 入口；持有 Python 命名空间、Computer 实例、执行守卫与输出回调；调用 SDK，不直接实现桌面动作。 |
| 内容交付 | `output.py` | OutputBuffer 限额与合并；ObservationOutput 暂存与去重；ObservationPresenter 保存差分基线。服务和 worker 各有独立输出缓冲，观察基线只在 worker。 |
| 内容交付 | `baseline_store.py` | 已交付基线的内存与临时文件存储；容量决定存放位置，失效与生命周期决定何时清除。 |
| 通信与平台适配 | `worker_transport.py` | worker 私有通道连接、Windows 客户端认证、帧编解码、兼容旧 JSONL、诊断批量发送；不管理进程或重试。 |
| 通信与平台适配 | `windows_worker.py` | Windows 端创建 worker、Job Object 绑定、认证监听与后代退出屏障；保持平台机制独立。 |
| 诊断记录 | `diagnostics.py` | IPC 与日志共用的诊断 JSON 编码；服务进程的 Journal 写 JSONL 日志及日志元数据。导入模块不创建日志，只有实例化 Journal 才创建。 |
| 可选桌面接入 | `desktop_bridge.py` | 在交互登录会话中创建完整 MCP 子进程，认证并转发 MCP stdio；不只是转发引擎到驱动的协议。它有独立 CLI 与连接生命周期。 |
