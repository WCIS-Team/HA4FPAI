# DEPLOYMENT

## Ubuntu 20.04 Linux 原生安装（当前交付入口）

推荐使用 arm64 `.deb` 或带 `install.sh` 的 Linux 压缩包。解包后执行 `sudo ./install.sh`，或者 `sudo apt install ./lang-server_0.1.0+20261104_arm64.deb`。系统依赖交给 apt；Python 依赖随包提供预编译 wheel，安装到专用虚拟环境；模型后台下载，默认全在线且 `--no-warm`。详细安装、目录、认证、下载状态和卸载说明见 [Linux 安装包](packaging/linux/README.md)。

下面保留的是旧工作站通过 SSH 部署到 `/mnt/data/lang` 的方式，不能代替 Linux 包管理安装；不要把两种布局混用。

## 旧工作站远程部署：模型装到磁盘，运行全在线

旧 Windows 入口 [deploy-board.cmd](deploy-board.cmd) 会交互输入目标板 SSH 密码。已有 SSH 公钥或环境变量 `LANG_DEPLOY_PASSWORD` 时，可直接运行下方 Python 命令。首次连接必须先核对并记录目标 SSH 主机指纹；确实要接受新主机时使用 `--accept-new-host-key`。

```powershell
python -X utf8 deploy.py --target board --profile online --models all
python -X utf8 deploy.py --apply --target board --profile online --models all --ask-password
```

第一条只检查环境并显示计划；第二条执行部署。默认目标 `192.168.1.113`、用户 `root`、目录 `/mnt/data/lang`；可用 `--host`、`--user`、`--root`、`--identity` 调整。默认 `--profile online --models all`。

**模型下载和链路运行是两件事**：部署 ASR/KWS/VAD/TTS/vocoder/**spk（CAM++ 声纹）** 文件及缺失的依赖，但链路固定为 `asr: remote-api`、`llm: openai`、`tts: edge`、`vad: none`、`kws: passthrough`、**`spk: none`**（声纹装文件但**不入链**：只有 `router`/`kws_route` 的 `verify` 或 `/api/v1/spk/*` 点名才装载）。分句使用 `tts_split`。服务带 `--no-warm`，不会预加载模型。验收只做包/文件/语法/端口/健康检查，不构造模型、不推理。用户报告 OOM 后，本入口拒绝 `--profile local --activate`。

## 自动完成的步骤

1. 检查 Python 版本、架构、可用空间、已有 Python 包、解码工具和下载站点连通性。
2. 先复用模型缓存或 `--model-source` 中非空、文件集合完整的模型，再用提供方官方 Python 客户端下载缺件；零字节 ONNX 文件不视为可用。
3. 仅补缺失的 Python 包，使用 `pip --only-binary=:all:`，不升级现有包，不在板上编译。
4. 复用现有 mpg123/ffmpeg/miniaudio 解码器，避免重复安装；保留板端可用的 Edge 解码配置。
5. 打包服务及其 split/KWS 路由、RMS、Edge 等实际依赖，并传输模型；传输前后校验部署包 SHA256。
6. 备份目标代码、配置和 systemd 单元，合并目标配置，保留目标机凭据，修正模型路径，输出权限为 0600 的部署覆盖配置。
7. 安装三个 systemd 单元、启用开机启动、重启服务，检查 HTTP 健康和单元状态。

本机使用已有 ssh/scp 或 Paramiko。缺少下载客户端时才安装官方客户端。目标 Python 至少 3.8；板端 aarch64 必须有兼容 wheel，缺 wheel 即报错，不回退源码构建。

## 模型来源与复用

默认安装已实现 CPU 后端的 ASR、KWS、Silero VAD、Matcha 中文 TTS、Vocos 声码器和 **CAM++ 声纹（spk）**；具体文件清单在 [deploy.py](deploy.py) 的 `MODELS`。**只装 fp32 那件**（本仓量化出的 `.int8.onnx` 不随包；要 int8 就自己拷并设 `spk_profiles.sherpa.prefer_int8=1`），另附模型自带的 `test_wavs/` 供板上冒烟。不装载它们。

```powershell
python -X utf8 deploy.py --apply --target board --models all --model-source S:/existing/model-root --ask-password
```

`--model-source` 可重复使用，接受模型根目录或单模型目录。Hugging Face 下载使用 `huggingface_hub`，直连失败后尝试同仓库镜像；可通过 `HF_ENDPOINT` 指定可达入口。也可显式使用 `--provider modelscope --model-repo NAME=OWNER/REPO` 指定 ModelScope 上同款模型。不会用 curl/wget 下载模型，也不会执行模型仓库内的脚本。

声码器来自 [k2-fsa 官方模型仓库](https://huggingface.co/k2-fsa/sherpa-onnx-models/tree/main/vocoder-models)，ASR 来自 [csukuangfj 官方发布](https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30)。**声纹（spk）和声码器没有 HF/ModelScope 镜像**，走 GitHub release：`gh release download speaker-recongition-models --repo k2-fsa/sherpa-onnx --pattern 3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx`（tag 拼写就是 `recongition`，改错即 404）；没装 `gh` 时用 `--model-source` 给现成目录。

**范围**：这里的“全部模型”指上述 CPU 后端模型，不是模型动物园全部候选。ERNIE/Qwen 的厂商 NPU 编译件、驱动和 tokenizer 交付包需要外部产物；不会自动编译或启用。**声纹档（`spk:sherpa`）已实装、可被点名**，但默认 `chain.spk: none`——装文件成功不等于它已在链里跑，也不等于门限/登记库已按你的场景调过。

`--models none` 只部署服务及在线运行依赖。模型是磁盘资产；已安装不等于已验证推理质量或已进入运行链。

## 凭据和配置

在线调用需要目标机自己的 API 配置。初次安装参考 [无密钥模板](config-examples/online.yaml)，在目标 `config/online.yaml` 填入凭据或使用后端支持的环境变量。该本地配置具名忽略，不再入库。脚本不会把控制机密钥复制到目标，也不在日志打印配置全文。

合并结果写入 `config/zz-deployment.yaml`，排序后覆盖原配置。默认运行全在线；原模型档参数保留供以后使用。私有覆盖文件必须保留 0600 权限，勿提交或分享。源模板与目标机配置不是同一份文件。

## 验收与回退

```bash
systemctl is-active lang-server-http lang-server-voice lang-server-loop
systemctl is-enabled lang-server-http lang-server-voice lang-server-loop
ss -lnt
```

HTTP 为 `0.0.0.0:18091`，Wyoming 为 `0.0.0.0:10300/10301/10302`。访问 `/health` 和 `/components` 确认选档；健康接口不代表在线供应商调用已验证，本部署不发送真实推理请求。

备份目录由命令输出，形如 `/mnt/data/lang-backup-UTC时间/`；部署包在本机 `workenv/lang-deploy/`。故障时停止三个 lang 单元，把备份的 `server/config/split/tts/models/edge/vad` 等代码目录和单元文件恢复到原位置，执行 `systemctl daemon-reload` 并重启。备份不包含新部署的大模型目录，不删除模型缓存，不影响其它视觉服务。恢复之前核对具体备份路径，避免覆盖后来修改。

## 本地网页

本次 Windows 恢复入口在 `workenv/lang-deploy/restore-local-20261104.ps1`，源码使用 [ggradio console](../web/ggradio/console.py)。网页端口 7861，托管 HTTP 为 18091；两者均绑定 0.0.0.0。控制台的健康探测通过 loopback 访问，避免把 wildcard bind 地址作为客户端目标。托管 HTTP 使用 `--no-warm`，默认全在线，不运行本地模型。

完整依赖说明见 [DEPENDENCIES](DEPENDENCIES.md)。
