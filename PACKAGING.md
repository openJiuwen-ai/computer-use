# 开发打包

在支持的目标主机上，以下命令生成包含本机后端的源码 wheel。
三端的源码和 editable 安装使用 PATH 中的 Rust 1.85.1 与目标架构的 CPython 3.11–3.15。macOS 驱动构建使用 Swift 6 工具链。
各平台的 wheel 随包提供对应 Python ABI 的无障碍读取扩展。

```bash
uv run --group build python -m build
```

如需分开构建和组包，在每个目标主机上构建驱动，再为要发布的每种 Python ABI 构建一份读引擎制品。将两类制品放入同一目录后，在组包主机运行组包命令。例如：

```bash
uv run --no-dev --group build python scripts/distribution.py backend
uv run --python 3.11 --no-project python scripts/sdk_native.py build --output .artifacts/backends
uv run --no-dev --group build python scripts/distribution.py wheels \
  --backend-dir /absolute/path/to/artifacts
```

后端制品默认生成在 `.artifacts/backends/`，wheel 默认输出到 `dist/`。
默认组装 macOS ARM64、Windows x64、Linux x86_64。可用 `--target` 指定单个平台。
如果下载的制品有外层 ZIP，先解包并将里面的 `backend-*.zip` 和 `sdk-native-*.zip` 放入制品目录。
组包校验制品的目标平台、协议、源码指纹和 Python ABI。

以下以 macOS ARM64 的 CPython 3.12 为例。驱动制品可以在同一平台的不同 Python ABI 间复用。

```bash
uv run --python 3.12 --no-project python scripts/sdk_native.py build --output .artifacts/backends
uv run --no-dev --group build python scripts/distribution.py wheels \
  --target macos-arm64 --python-tag cp312 --backend-dir .artifacts/backends
```

`--python-tag` 默认是 `cp311`。将匹配源码指纹的 `sdk-native-*.zip` 和 `backend-*.zip` 放在同一个制品目录，即可在组包主机上生成对应平台和 ABI 的 wheel。
