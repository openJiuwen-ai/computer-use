# 开发打包

在支持的目标主机上，以下命令生成包含本机后端的源码 wheel。

```bash
uv run --group build python -m build
```

如需先构建后端再组装 wheel，在目标原生主机构建制品，将各平台制品放入同一目录，然后运行组包命令。

```bash
uv run --no-dev --group build python scripts/distribution.py backend
uv run --no-dev --group build python scripts/distribution.py wheels \
  --backend-dir /absolute/path/to/backends
```

后端制品默认生成在 `.artifacts/backends/`，wheel 默认输出到 `dist/`。
默认组装 macOS ARM64、Windows x64、Linux x86_64。可用 `--target` 指定单个平台。
从 GitCode 下载制品时，解开平台生成的外层 ZIP，将里面的 `backend-*.zip` 放入后端目录。
组包只接受匹配当前平台和源码指纹的后端制品。

## GitCode Actions

- `native-backends` 每天北京时间 03:00、后端相关变更进入 `main` 或 `master` 时运行，也可手动触发。它构建三端后端并在同一轮运行组装 wheel，制品保留 30 天。
- `sdk-package` 可手动复用匹配的历史后端制品。也可在本机设置只读 `GITCODE_TOKEN` 后运行 `scripts/distribution.py wheels --repository openJiuwen/computer-use`，用实际产出制品的仓库名替换示例。

在仓库开启 Actions。Linux 在托管 Runner 构建，macOS 和 Windows 通过 SSH 在对应的原生构建主机执行。
主机需配置对应平台的编译工具链、Python 3.12、uv 和 SSH。配置以下 Actions secrets。

| 名称 | 用途 |
| --- | --- |
| `{MACOS,WINDOWS}_BUILDER_HOST` | 构建主机的 `user@host`。 |
| `{MACOS,WINDOWS}_BUILDER_SSH_KEY` | SSH 私钥。 |
| `{MACOS,WINDOWS}_BUILDER_KNOWN_HOSTS` | 经确认的 SSH 主机公钥记录。 |
| `BACKEND_ARTIFACT_TOKEN` | 手动复用历史制品所需的 GitCode 只读 token。 |

可以设置对应的 `_BUILDER_PORT`、`_BUILDER_PYTHON` 和 `_BUILDER_UV` 变量。
首次配置后手动运行 `native-backends`，确认三份后端制品和 `sdk-wheels` 均已生成。
