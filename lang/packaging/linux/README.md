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

## 后台服务具体做什么

安装后启用三个常驻服务和一个独立下载任务：

| 单元 | 工作 | 默认是否加载本地模型 |
|---|---|---|
| lang-server-http | HTTP API，监听18091 | 否，remote-api/openai/edge，带 --no-warm |
| lang-server-voice | Wyoming wake/ASR/TTS接口，监听10300/10301/10302 | 否，复用在线选档，带 --no-warm |
| lang-server-loop | 麦克风/扬声器常驻循环，有设备时采集音频并交在线链处理 | 否，带 --no-warm；无此需求可停用该单元 |
| lang-server-model-download | 下载并检查五组CPU模型文件 | 不构造或执行模型；成功后退出，失败每300秒重试 |

日志用 `journalctl -u 单元名` 查看。下载结果是模型目录内的 `download-status.json`，不是离线推理可用性证明。

## 安装覆盖范围与外部产物

**当前发布的0.1.1并不是全部离线组件安装包**。自动下载清单只有 [models.json](models.json) 的五组CPU资产：ASR、KWS、Silero VAD、Matcha TTS、Vocos；全部使用Hugging Face官方客户端获取，支持同仓库镜像回退。

| 组件 | 获取来源/所需产物 | 当前包是否自动处理 |
|---|---|---|
| 五组CPU模型 | manifests指定的HF仓库与文件 | 自动下载文件，不自动启用 |
| Ekho | [Linux源码fork](https://github.com/takomitkm/ekho-Linux)；[SourceForge语音源](https://sourceforge.net/projects/e-guidedog/files/Ekho-Voice-Data/0.2/)；可复用匹配目标的预编译kit | 不自动安装引擎、语音库或Ekho单元 |
| ERNIE-4.5-0.3B-PT | 原始来源为ModelScope PaddlePaddle/ERNIE-4.5-0.3B-PT；还需要目标NPU编译件、tokenizer、驱动和SDK | 不下载或编译NPU产物，不安装其运行链 |
| Qwen3-0.6B | 原始来源为ModelScope/HF Qwen/Qwen3-0.6B；还需要目标NPU编译件与驱动 | 不下载或编译NPU产物，不安装其运行链 |
| Qwen2 | 厂商ModelScope AIBS/Qwen2_0.5B_zg3.36.1_axi_demo配方/ONNX；底座Qwen/Qwen2-0.5B-Instruct | 不把厂商版本产物视作可在任意SDK直接运行 |

Ekho不是一份HF权重。最小可用交付需要兼容aarch64/glibc2.31的 `ekho-server`、完整匹配的 `pinyin.index/pinyin.voice`、字典和字母数据，以及所用构建路线的espeak数据/共享库闭包。Git没有携带大体积voice数据；已有 `scripts/fetch-voice.sh` 只说明原始44.1k/16k语音数据的获取和后续生成/转换流程，当前deb没有调用它，不能把源码clone或原始音频下载当作Ekho已可用。现有手工 `lang-ekho.service` 使用旧机路径，也没有集成进本包。

三个LLM的编译需要区分两件事：

1. **模型目标编译**：原始权重/ONNX按各自迁移配方经过iCraft的parse、optimize、quantize、adapt、generate，生成匹配ZG330（target=zhuge）的模型产物。它不是gcc交叉编译，普通HF snapshot不能直接替代已编译的 `_ZG.json/.raw`。
2. **本机程序构建**：如果还需要构建C/C++驱动或运行库，才涉及aarch64工具链与Ubuntu20.04 sysroot的交叉编译。已有Python驱动本身不需交叉编译，但依赖匹配的板端icraft CP38 wheel、SDK、固件及模型布局。

已有经核验的模型/程序产物可以复用，不必每台设备重新编译；应核对NPU target、iCraft/运行库版本、模型输入输出与KV布局、tokenizer、CPU ABI及产物校验值。仓库的模型迁移README和TOML是各自配方入口，不能仅凭目录名判断可用。

**安装器应把这些列为“本包未管理的外部组件”，不能只报“全部模型已安装”。** 新源码的postinst已补覆盖范围与构建前提提示；apt安装不等待用户交互，不自动拉起交叉编译、不在目标板构建，也不尝试加载或验证这些模型。已发布的旧deb不会因源码修改自动改变行为，需后续打包发布才带上新提示。

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
