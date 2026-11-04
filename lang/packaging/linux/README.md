# Linux 安装包

本交付针对 **Ubuntu 20.04 LTS / arm64（aarch64）/ Python 3.8**，适用于 113 开发板同类系统。默认全部走在线链路；本地模型只下载到磁盘，不加载、不预热、不进行推理测试。此安装包不包含 Windows CMD 入口。

## 解包后运行

```bash
tar -xzf lang-server_0.1.0+20261104_arm64_ubuntu20.04.tar.gz
cd lang-server_0.1.0+20261104_arm64
sudo ./install.sh
```

脚本检查系统版本、架构和空间，验证相邻 deb 的 SHA256，更新 apt 索引，再用 apt 安装该 deb 及缺失系统依赖。它支持从任意工作目录调用，路径按脚本所在目录确定。普通用户可直接执行，脚本会调用 sudo；没有 sudo 时请先切 root。

也可以直接使用 deb：

```bash
sudo apt update
sudo apt install ./lang-server_0.1.0+20261104_arm64.deb
```

请使用 `apt install ./文件.deb`，这样依赖由包管理器解析；单独 `dpkg -i` 不负责下载缺失依赖。

## 安装内容

- apt 依赖：Python 3.8、python3-venv、ca-certificates、alsa-utils、mpg123、adduser、init-system-helpers。
- 随包附带兼容 arm64/Python 3.8 的预编译 Python wheel 和带 SHA256 的固定版本清单；安装到专用虚拟环境，不使用系统 pip、不编译源码、不访问 PyPI。
- Python 组件包含 edge-tts、Wyoming、sherpa-onnx/core、NumPy、Hugging Face 官方客户端及其传递依赖。只安装文件，不导入模型运行时。
- 模型下载为独立后台任务：ASR、KWS、Silero VAD、Matcha TTS、Vocos，共五组约 308 MiB；使用官方客户端、单下载线程，优先复用完整文件，支持失败后重试。

安装 deb 本身不等待模型下载。下载网络不可用不会挡住在线服务安装；apt 系统依赖仍需要可达的软件源或预先安装。自动下载的模型目录是 `/var/lib/lang-server/models`，不会覆盖旧 `/mnt/data/lang` 的模型。

包内不附带 API 密钥。ASR/LLM 在线调用需要你在目标机配置认证；安装服务成功不代表云端认证或供应商响应已验证。

## 配置与状态

| 用途 | 位置 |
|---|---|
| 程序 | `/usr/lib/lang-server/lang` |
| Python 私有环境 | `/var/lib/lang-server/venv` |
| 组件配置 | `/etc/lang-server/config/` |
| 在线 API 配置 | `/etc/lang-server/config/online.yaml` |
| 可选凭据/代理环境文件 | `/etc/lang-server/lang-server.env` |
| 模型文件 | `/var/lib/lang-server/models/` |
| 下载进度与结果 | `/var/lib/lang-server/models/download-status.json` |

例如在环境文件中设置供应商使用的环境变量（用自己的值）：

```bash
sudoedit /etc/lang-server/lang-server.env
# SILICONFLOW_API_KEY=your-key
sudo systemctl restart lang-server-http lang-server-voice lang-server-loop
```

默认运行档：`asr=remote-api`、`llm=openai`、`tts=edge`、`vad=none`、`kws=passthrough`、`spk=none`。三个服务都使用 `--no-warm`，在线选档覆盖文件由包维护。已有在线配置和密钥在升级时保留。

HTTP 监听 `0.0.0.0:18091`，Wyoming 监听 `0.0.0.0:10300/10301/10302`。安装不会额外启动浏览器或网页试验台；HTTP 根路径和接口由服务提供。

```bash
systemctl status lang-server-http lang-server-voice lang-server-loop
journalctl -u lang-server-model-download -f
sudo systemctl restart lang-server-model-download
```

模型下载成功后 oneshot 单元可能显示 inactive，这是正常结束；以下载状态 JSON 和日志为准。内存上限为 160 MiB，后台下载不做模型构造或推理。网络代理/同仓库镜像可通过环境文件的 `HTTPS_PROXY` / `HF_ENDPOINT` 配置。

若暂不下载模型，解包安装入口支持：

```bash
sudo ./install.sh --no-model-download
```

恢复下载时删除目标配置目录下的 `no-model-download` 标记，然后 `sudo systemctl enable --now lang-server-model-download`。此开关只控制下载，不改变在线运行档。

## 升级和卸载

```bash
sudo apt install ./较新版本_arm64.deb
sudo apt remove lang-server
```

升级保留目标机配置与模型；remove 停止服务并移除包管理的文件，保留配置和下载资产。purge 可清理包生成的配置/私有环境，模型仍保留，避免删除用户缓存。原来的 `/mnt/data/lang` 不属于此包，不自动迁移或删除。

同名旧服务若由 `/etc/systemd/system/` 手工安装，会覆盖包提供的 `/lib/systemd/system/` 单元；安装器会在发现旧单元冲突时提示处理，避免把旧服务误判为新包已生效。应先备份旧设置并明确迁移，不能让两套服务同时争用固定端口。

## 范围和验证

这份交付面向 Ubuntu 20.04 arm64，amd64 包仅用于工作站容器中的安装流程验证。模型下载成功只表示文件就绪；本轮不执行本地模型。NPU ERNIE/Qwen 编译件、CAM++ 模型不属于这五组自动下载清单，也不会在开发板编译。

包构建与生命周期测试在工作站完成。目标架构 wheel 标签及依赖闭包经过静态校验；不把 amd64 容器测试称作 arm64 真机运行验证。服务的在线请求仍需目标网络和用户自己的认证。

## GitHub 自动打包

仓库 Actions 页选择 **Build lang Ubuntu 20.04 packages**，点击 Run workflow 即可；可选填 Debian 版本，不填则按运行编号生成。master 上的 lang 改动及 pull request 会自动构建。普通构建的下载文件在该次运行的 Artifacts，保留30天：`lang-ubuntu20.04-arm64` 是目标板安装包，`lang-ubuntu20.04-amd64` 用于容器测试。

推送形如 `lang-v0.1.1` 的标签时，全部检查成功后会自动创建 GitHub Release，附上 arm64 deb、Linux压缩包和 SHA256。版本取标签的 `lang-v` 后缀。这里只构建与发布文件，不会自动安装到113或任何生产设备。

流水线从固定版本/哈希的 `requirements-arm64.lock`、`requirements-amd64.lock` 获取预编译 wheel，运行静态审计，构建包，并在Ubuntu20.04 amd64容器里检查安装/重装/卸载与在线HTTP接口。测试禁止导入模型库、构造组件、预热或推理。

**模型权重不进入 Git、不在 CI 下载。** 安装包会启用目标系统的模型下载后台任务，通过官方客户端准备五组模型；离线或下载失败不阻塞在线服务启动，且默认不加载本地模型。

可复现构建入口为 `prepare_wheels.py` 和 `build_deb.py`；中间 staging、wheelhouse、测试容器记录和最终产物放在工作目录或 GitHub runner 临时目录，不进入代码仓库。
