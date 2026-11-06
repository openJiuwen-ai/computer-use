# 开发包与后端流水线制品

本流程只上传 GitCode Actions 的流水线制品，不上传 PyPI，不创建 Release，也不创建或推送 Git 标签。下载到本地的 SDK wheel 已包含对应后端，安装及运行不再依赖制品的保留期限。

## 构建与组包

- `.gitcode/workflows/native-backends.yml`：每天北京时间 03:00 构建；`main`、`master` 的后端相关变更也会触发，支持手动运行。三端后端分别通过 `upload-artifact` 保留 30 天，随后在 Linux 上组装 SDK wheel，并将 wheel 作为 `sdk-wheels` 制品上传。
- `.gitcode/workflows/sdk-package.yml`：手动运行，复用历史流水线中匹配的后端，为当前 SDK 组包。不需要重新调用三台构建机。
- 默认交付 macOS 14+ ARM64、Windows x64、Linux x86_64 三个 wheel。工具也支持 macOS x86_64、Linux AArch64，可在对应原生主机上使用 `--target` 构建，再为该目标组包。

后端制品名包含目标平台及原生输入的 SHA-256。输入包括平台源码、构建脚本、相关指针资源和协议版本；普通 SDK 业务代码或 SDK 版本号变化不会使后端失效。组包只接受与当前源码、协议、平台及文件校验和匹配的制品，不会取一个任意的“最新版”。不匹配或已过期时明确失败，应先运行后端流水线。

Linux 后端是 Python 包，可在任意构建平台生成其 wheel；不搬运不能跨电脑复用的 `.venv`。目标桌面仍须安装 AT-SPI、PyGObject 等系统依赖，并运行 `computer-use-build` 准备运行环境。macOS、Windows 的 SDK wheel 直接携带已构建的驱动及必要组件，`computer-use-build` 直接复用它们；不需要在使用者电脑安装 Swift 编译工具链。

## 一次性配置 GitCode

先在仓库开启 Actions，并确认项目可使用官方的 `checkout`、`setup-python`、`upload-artifact`、`download-artifact` 插件。

GitCode 官方托管 Runner 当前提供 Linux 环境。这里采用 Linux Runner 编排、SSH 调用 macOS / Windows 原生构建主机的方式。主机可以是已有机器，也可以是租用的主机；不需要每天人工登录出包，但定时构建时必须在线且能被 Runner 访问。这里没有配置不存在的 `macos-latest`、`windows-latest` 托管 Runner。

构建机准备：

| 主机 | 一次性要求 |
| --- | --- |
| macOS | ARM64、macOS 14+、已完成初始化的 Swift 6 / Xcode 工具链、Python 3.12、uv、SSH 登录。构建不需要桌面访问授权。 |
| Windows | Windows x64 10 1903+、.NET Framework 4.8 及系统 WinRT 元数据、Python 3.12、uv、OpenSSH Server。 |
| Linux | 直接使用 GitCode Ubuntu x64 Runner；打包 Python 后端不需要图形会话。 |

在“项目设置 → Actions 密钥与变量”配置：

| 类型 | 名称 | 内容 |
| --- | --- | --- |
| Secret | `MACOS_BUILDER_HOST` / `WINDOWS_BUILDER_HOST` | 对应构建机的 `user@host`。 |
| Secret | `MACOS_BUILDER_SSH_KEY` / `WINDOWS_BUILDER_SSH_KEY` | 对应 SSH 私钥，多行原文。 |
| Secret | `MACOS_BUILDER_KNOWN_HOSTS` / `WINDOWS_BUILDER_KNOWN_HOSTS` | 经确认的 SSH 主机公钥记录，非默认端口需使用 `[host]:port` 格式。 |
| Secret | `BACKEND_ARTIFACT_TOKEN` | 可读取当前仓库 Actions 制品的 GitCode token，供独立 SDK 组包流水线使用。 |
| Variable，可选 | `MACOS_BUILDER_PORT` / `WINDOWS_BUILDER_PORT` | SSH 端口，默认 22。 |
| Variable，可选 | `MACOS_BUILDER_PYTHON` / `WINDOWS_BUILDER_PYTHON` | SSH 会话可调用的 Python 命令，默认 `python3` / `python`。此入口路径不支持空格；可使用 PATH 中的命令。 |
| Variable，可选 | `MACOS_BUILDER_UV` / `WINDOWS_BUILDER_UV` | SSH 会话可调用的 uv 路径，默认 `uv`；可指定含空格的绝对路径。 |

SSH 主机身份校验始终开启。私钥仅临时放入 Runner 的私有文件，不会发送给原生构建机或写入制品。原生构建机收到当前提交的源码 ZIP，在临时目录构建并传回结果，结束后清理目录。流水线只监听主分支及手动/定时事件。

首次配置后，手动运行 `native-backends`，确认三个 `backend-*` 制品和 `sdk-wheels` 都已生成。日后仅修改 SDK 时可手动运行 `sdk-package`，或在任意 Python 开发机上运行下面的组包命令。默认分支若不叫 `main` / `master`，需要修改后端流水线的分支过滤。

## 本地复用流水线制品

将只读 token 放入环境变量 `GITCODE_TOKEN`，然后运行：

```bash
uv run --no-dev --group build python scripts/distribution.py wheels \
  --repository openJiuwen/computer-use
```

`--repository` 指向实际产出后端制品的仓库，也可通过 `GITCODE_REPOSITORY` 指定；流水线内默认使用 `ATOMGIT_REPOSITORY`。输出位于 `dist/`。只打一个目标时添加 `--target macos-arm64`，多个目标可重复传入 `--target`。

下载使用 GitCode Artifact REST API，在历史运行中分页查找完整匹配的制品名，跳过过期记录，校验后端包中的每个文件。API token 只发送给 GitCode，重定向到对象存储时移除认证头。SDK wheel 内的 `computer_use/_resources/backend.json` 保留后端目标、协议、源码指纹、构建提交和文件校验信息。

也可以从流水线页面下载各端后端制品，**先解开 GitCode 外层 ZIP**，将内部的 `backend-<target>-<fingerprint>.zip` 放进同一目录，然后离线组包：

```bash
uv run --no-dev --group build python scripts/distribution.py wheels \
  --backend-dir /absolute/path/to/backends
```

“离线”指不访问 GitCode；Python 构建工具及依赖仍需提前缓存或可访问包源。

## 手动构建单端后端

在目标原生平台的源码仓库内运行：

```bash
uv run --no-dev --group build python scripts/distribution.py backend
```

默认写入 `.artifacts/backends/`。需要查看当前源码要求的制品名时：

```bash
python scripts/distribution.py name --target macos-arm64
```

普通源码 wheel 仍可用 `uv run --group build python -m build` 生成，保持原有在目标电脑上构建后端的方式。带后端的 wheel 必须使用上述 `wheels` 命令组装，避免构建目录残留或平台标签错误。

官方说明：[上传和下载制品](https://docs.gitcode.com/docs/help/home/org_project/pipeline/writing-pipelines/upload-download-artifacts/)、[Artifact 列表 API](https://docs.gitcode.com/docs/apis/get-api-v-8-repos-owner-repo-actions-artifacts/)、[Artifact 下载 API](https://docs.gitcode.com/docs/apis/get-api-v-8-repos-owner-repo-actions-artifacts-artifact-id-archive-format/)、[托管 Runner](https://docs.atomgit.com/docs/help/home/org_project/pipeline/runner-management/using-hosted-runners/)。
