# lang/server/deploy

本目录维护三个 systemd 模板。完整安装流程见 [DEPLOYMENT](../../DEPLOYMENT.md)，依赖见 [DEPENDENCIES](../../DEPENDENCIES.md)。Windows 可双击 [deploy-board.cmd](../../deploy-board.cmd)。

| 单元 | 入口 | 固定监听 |
|---|---|---|
| lang-server-http | app.py（**启动即 warm**） | 0.0.0.0:18091 |
| lang-server-voice | wyoming_gateway.py（**启动即 warm**） | 0.0.0.0:10300/10301/10302 |
| lang-server-loop | loop.py --run（**启动即 warm**） | 无监听端口，麦克风闭环 |
| **lang-ekho** | ekho-server（纯 C++，外部 TTS） | **127.0.0.1**:2046（只绑本机，无鉴权） |

三个 lang-server 单元都是 `Restart=always`；`MemoryMax` 分别 480M / 600M / 480M /（ekho 96M）。
**lang-server 三个上限相加超 993 MB 物理内存，别同时 enable 三个。**

准入闸在 `app.py` 进程内（`server.max_concurrent: 1`，`server.gate_timeout_s: 30`）：
只串行化本服务的链请求，**不跨进程**（跑视频不受影响）；满且超时回 `code 429`。
`/stats` 里的 `gate` 段可看并发/排队/超时计数。

默认部署树是 `/mnt/data/lang/server`、`/mnt/data/lang/config`、`/mnt/data/lang/split` 以及相邻模型/后端目录，不能把 server 平铺到旧 `/mnt/data/langserver`。Edge、RMS 和 KWS 路由依赖相邻组件；一键工具按实际依赖一起复制。

模板使用 `LANG_CONFIG_DIR=/mnt/data/lang/config`，凭据环境文件可放 `/etc/lang-server.env`。日志位于 server 目录。工具会备份原单元，保留现有环境设置和内存上限，再更新代码路径。

**默认全在线**：安装模型文件和缺失依赖后，服务**启动即预热**（sherpa runtime + active 档），
所以验收要等日志出现 `预热: sherpa=ok ...`（或 `/health` 的 stages 全部就绪）再断言；
不要再把"模型未装载"当成"推理已通过"。

```bash
systemctl is-active lang-server-http lang-server-voice lang-server-loop
systemctl is-enabled lang-server-http lang-server-voice lang-server-loop
```

端口及选档以 `config/server.yaml` 和目标 `config/zz-deployment.yaml` 为准。在线供应商认证由目标机自己的配置或环境变量提供，禁止把本地密钥带进部署包。其它视觉服务不在本工具管理范围内。
