# 开发打包

先按[源码构建指南](docs/build-from-source.md)准备目标系统的工具链、运行依赖和所需 CPython 版本。wheel 包含对应 Python ABI 的无障碍读取扩展；为不同平台或 Python 小版本分别出包。

在目标主机上执行以下命令构建源码发行包和 wheel。若源码 wheel 的驱动构建失败，仍可安装 SDK；应先检查构建状态并修复驱动问题，再在目标桌面使用。

```bash
uv run --python 3.12 --no-dev --group build python -m build
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
