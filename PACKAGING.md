# 开发打包

从源码生成、安装和验证单个平台的 wheel，请按[源码构建指南](docs/build-from-source.md)操作。若还需生成源码发行包（sdist），在该指南第 2 步的最后一条命令中省略 `--wheel`。

正式发布使用经过校验的驱动和 Rust 制品。wheel 包含对应 Python ABI 的无障碍读取扩展，为不同平台或 Python 小版本分别出包。

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

读引擎构建时使用固定版本的 Unicode 数据生成字符查找表。首次构建会下载并缓存 Unicode 数据及其许可，以及两份 Rust 工具链依赖的声明。
读引擎制品与最终 wheel 都携带生成的 `THIRD_PARTY_LICENSES.txt`，组包主机无需重新获取这些依赖。

以下以 macOS ARM64 的 CPython 3.12 为例。驱动制品可以在同一平台的不同 Python ABI 间复用。

```bash
uv run --python 3.12 --no-project python scripts/sdk_native.py build --output .artifacts/backends
uv run --no-dev --group build python scripts/distribution.py wheels \
  --target macos-arm64 --python-tag cp312 --backend-dir .artifacts/backends
```

`--python-tag` 默认是 `cp311`。将匹配源码指纹的 `sdk-native-*.zip` 和 `backend-*.zip` 放在同一个制品目录，即可在组包主机上生成对应平台和 ABI 的 wheel。

## macOS 官方发布签名

在项目的 Mac 发布机上，将组织的 **Developer ID Application** 证书及私钥配置在构建账号的钥匙串。
将官方 Apple Team ID 配置为 CI 变量 `APPLE_TEAM_ID`，将该证书的指纹配置为
`CUA_MACOS_SIGN_IDENTITY`。私钥由发布机保管。下例生成并验证 ARM64 / CPython 3.11 的 wheel。

```sh
: "${APPLE_TEAM_ID:?Set the official Apple Developer Team ID}"
: "${CUA_MACOS_SIGN_IDENTITY:?Set the Developer ID Application signing identity}"
export CUA_MACOS_SIGN_IDENTITY
uv run --no-dev --group build python scripts/distribution.py backend \
  --target macos-arm64 --macos-release-team "$APPLE_TEAM_ID"
uv run --python 3.11 --no-project python scripts/sdk_native.py build --output .artifacts/backends
uv run --no-dev --group build python scripts/distribution.py wheels \
  --target macos-arm64 --python-tag cp311 --backend-dir .artifacts/backends \
  --macos-release-team "$APPLE_TEAM_ID" --output dist/macos-release
```

原生构建先签好 `Jiuwen CUA.app`，启用 hardened runtime 和安全时间戳，再生成后端文件哈希。
组包在 Mac 上取出最终 wheel 内的 App，校验签名完整性、标识 `com.openjiuwen.cua`、
Developer ID Application 证书类型和预期 Team ID。验证通过才将新 wheel 移入输出目录。
云构建入口可用环境变量 `CUA_MACOS_RELEASE_TEAM_ID` 指定相同的发布校验。

如果 wheel 在其他系统组装，将生成的候选 wheel 交给 Mac 发布步骤，针对待上传的同一批文件运行：

```sh
python3 scripts/macos_signing.py --team-id "${APPLE_TEAM_ID:?}" dist/macos-release/*macosx*.whl
```

发布任务只在该命令成功后上传已验文件。首次发布和更换签名团队时，另在一台测试 Mac 上安装 wheel，
授予辅助功能和录屏权限，并以一次窗口元素读取和一次截图验证实际使用效果。
