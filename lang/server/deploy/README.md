# lang/server/deploy —— 板上 systemd 单元

语音**不再由 HA4FPAI 起**，改由本服务起（她 2026-11-04 定）。HA4FPAI 侧对应的
`klar-voice-{gateway,asr,tts,wake}.service` 已退掉并归档在
`HA4FPAI/archiver/ha4fpai-voice/deploy/`。

| 单元 | 起什么 | 端口 |
|---|---|---|
| `lang-server-voice.service` | `wyoming_gateway.py`（wake/asr/tts 三合一进程） | 10300 / 10301 / 10302 |
| `lang-server-http.service` | `app.py`（HTTP 面，可选） | 18091 |
| `lang-server-loop.service` | `loop.py --run`（fsm 真实语音循环，一麦一喇叭自动匹配；无麦常驻重探，硬件到位即工作） | 无端口 |

> **板上实况路径与本文档不符处（2026-11-04 记）**：板上实际布局是 `/mnt/data/lang/{server,config}`（镜像 lang 树父子结构，edge 后端按上级目录相对定位，平铺会断），本文档与单元模板里写的 `/mnt/data/langserver` 为旧口径；板上已装的 `lang-server-*.service` 用的是实况路径。待与并发会话对齐后统一。

相关件：验收客户端 `tools/wyoming_probe.py`、板端验收脚本 `tests/smoke_board.sh`（从 HA4FPAI 移交）。

## 部署到板（`/mnt/data/langserver`）
```sh
# 1) 推送（从工作站，不在板上编译）
BOARD=root@192.168.1.113
#   py 与 config：core.py cfg.py fsm.py app.py wyoming_gateway.py backends/ tests/
#   config 目录整体：config/
rsync/scp 到 $BOARD:/mnt/data/langserver/（含 config/）
# 2) 板上依赖（现成，不装新东西最好）
#   python3 -c 'import wyoming, sherpa_onnx'   # 需已装（HA4FPAI 板早已有）
# 3) 装单元
sudo cp deploy/lang-server-voice.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lang-server-voice
sudo systemctl status lang-server-voice --no-pager
```

## 注意
- 模型路径在 `config/*.yaml` 里指 `/mnt/data/models/...`（与旧 `klar-voice.json` 一致，模型不用动）。
- 密钥不进 config（D-27），走环境变量（`MIMO_API_KEY` 等）。
- 这套**未在板上验证过**（本服务目前只在 PC py3.13 自检）；先手动 `python3 wyoming_gateway.py` 跑通再上 systemd。
- `MemoryMax` 沿用旧值参考，按实测调。
