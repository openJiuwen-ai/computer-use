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
如果下载的制品有外层 ZIP，先解包并将里面的 `backend-*.zip` 放入后端目录。
组包只接受匹配当前平台和源码指纹的后端制品。
