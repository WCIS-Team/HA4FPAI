# ha-cut

更新：2026-11-06。本清单是ha-small的功能与裁剪步骤正本；只要求主要功能，不复制HA4FPAI的46项组件落地方案。已完成步骤当场勾选，未验证的硬件/外部服务保持空框。

## 一、目标与边界

- 归属：HA4FPAI 的 `ha-small` 分支材料（orphan 分支，与 master 无共同历史）。**2026-11-07 已获用户授权推送到远端**：`origin/ha-small` @ `c3f5235`，与本地一致。本机 `repos/ha-small` 只是暂存副本（被 `repos/.gitignore` 忽略），不是远端。
- 独立位置：`S:/work/WCIS/repos/ha-small`；HA4FPAI 现有 `master` 与 `archive-2026-11-06` 及其 `lang/npuserv` 不改动。
- 官方源码基线：Home Assistant Core 2026.10.4的PyPI sdist，SHA256与URL见[来源](UPSTREAM.md)。不是从HA4FPAI archive复制一套自研控制层。
- 原则：先删除；保留文件按上游哈希核对；只有实际运行证明确实无法靠恢复必要依赖解决时才考虑补丁。每个补丁必须记录原文件、理由、替代方案和功能验证。
- HA现有框架/注册表、认证、自动化、Recorder等沿用原设计，不要求替换成HA4FPAI的SQLite业务schema或worker架构。
- Python>=3.14.2是本基线的实际前提；裁文件不会使它兼容板端Python3.8。不为短期演示删版本门禁。

## 二、已完成裁剪与来源步骤

- [x] **CUT-01｜检查已有方案与环境。** 本机有Podman和Python3.14；旧HA裁剪文档只作参考，未采用其中校机OS/板端资源的旧推断。
- [x] **CUT-02｜取得官方源码并验签名摘要。** 下载38675928B sdist，SHA256与官方PyPI元数据一致，保留原archive及元数据。
- [x] **CUT-03｜保留上游署名和许可。** LICENSE.md、上游README.rst及各保留文件原样；新增本项目README/来源说明，未冒称官方发行版。
- [x] **CUT-04｜建立功能保留集。** 覆盖设备控制、状态事件、自动化、网页/API、人员/区域、历史、语音、视频与通用连接接口。
- [x] **CUT-05｜求依赖闭包。** 遍历manifest依赖/after_dependencies及绝对、相对静态导入，补齐基础实体域；最终128个集成（2026-11-07 三轮方法修正后按三条规则删去 plex），不依靠手工猜61个名字。
- [x] **CUT-06｜执行删除。** 删除1395个集成及对应不保留测试、重新生成的旧发行metadata；全量删除文件清单保存在workenv，裁剪摘要保存在cut/cut-report.json。
- [x] **CUT-07｜证明保留源码未改。** 6096个上游文件有逐文件SHA256；修改数0，补丁清单空。新增说明、例子、锁文件与校验工具独立登记。
- [x] **CUT-08｜不加载完整默认配置。** 保留default_config源码以满足bootstrap预导入，但实验配置不启用default_config；运行测试确认未加载。
- [x] **CUT-09｜隔离构建与安装。** 本机Linux容器内由未改pyproject构建wheel并安装，pip check通过；不改系统Python、不影响现役HA4FPAI或板端。
- [x] **CUT-10｜解决原生运行依赖。** 按实际报错补容器g++/make/cmake/pkg-config/libspeexdsp-dev构建两个语音依赖，并补libturbojpeg0/ffmpeg；不是修改HA源码绕过功能。
- [x] **CUT-11｜保存来源/删除/依赖与验证说明。** 来源、逐文件审核、功能探针、锁文件和启动方法均随本目录提供；日志和临时产物留workenv。

## 三、主要功能验收

验证环境：Linux x86_64，Python3.14.10。30项功能检查与4项正常进程重启检查；具体结果在workenv/ha-small/results。以下完成只表示列出的功能范围。

- [x] **FUNC-01｜网页入口与登录认证。** 前端HTML返回正常；未认证API返回401；真实onboarding建立测试用户并换取token；认证API可用。未完成真实浏览器全页面操作验收。
- [x] **FUNC-02｜状态与服务。** REST读取实体状态，调用服务切换input_boolean，服务注册列表包含主要功能。
- [x] **FUNC-03｜事件与WebSocket。** WebSocket认证、订阅自定义事件、经REST触发并收到同nonce事件。
- [x] **FUNC-04｜实体/区域注册入口。** 经WebSocket读取实体注册表与区域注册接口；不是新造一套注册表。
- [x] **FUNC-05｜脚本。** script调用实际改变测试实体。
- [x] **FUNC-06｜自动化。** 测试实体状态触发counter递增，验证事件到动作执行。
- [x] **FUNC-07｜场景。** scene恢复测试实体到off。
- [x] **FUNC-08｜模板。** 模板sensor跟随被观察实体状态改变。
- [x] **FUNC-09｜定时与辅助状态。** timer从active到idle；数字/文本helper可写，counter可操作。
- [x] **FUNC-10｜人员与区域。** person与zone实体实际建立并可通过API查询。此项不是人脸/声纹融合身份验收。
- [x] **FUNC-11｜SQLite历史。** Recorder记录状态变化，history API查到多条记录，SQLite quick_check通过。
- [x] **FUNC-12｜外部设备HTTP控制通道。** HA rest_command实际POST到隔离stub，服务端收到正确参数且无重复调用。具体视频/声纹服务接线另验。
- [x] **FUNC-13｜Assist文本意图到动作。** 显式通过HA官方暴露接口允许测试实体后，文本“turn on Cut Test”返回action_done并改变实体，不将HTTP200/错误话术当成功。
- [x] **FUNC-14｜语音框架。** conversation、assist_pipeline、stt、tts均实际加载；原生音频处理依赖可导入。实体暴露策略沿用HA，不默认向助手开放所有实体。
- [x] **FUNC-15｜正常重启后的持久化。** 正常SIGTERM并重新启动相同配置；既有认证仍可用、文本helper恢复、SQLite历史保留及完整性通过。不是断电恢复测试。
- [x] **FUNC-16｜验证删除确实生效。** tuya等无关集成在安装树中不存在；保留的默认配置模块没有被加载。

## 四、保留但尚未连接/验收的主要能力

这些代码已保留在依赖闭包里；不因源码存在、模拟闭环或服务端框架启动就勾成真实硬件已可用。2026-11-07 在本机隔离容器完成了协议级/软件级验证，按子项分开勾选：

- [x] **CONNECT-01a｜Wyoming TTS 协议与音频链路。** 自建协议正确的 Wyoming TTS 服务（`cut/wyoming_tts.py`）：`describe` 返回 `ha-small-tone-tts`/`cut-tone`，`synthesize` 返回 audio-start→26 个 audio-chunk→audio-stop 共 **26,460 B PCM**（5/5）。HA 侧加 wyoming 配置条目 → 实体 `tts.ha_small_tone_tts` → `/api/tts_get_url` 用 **`engine_id`**（不是 `platform`）→ 下载到 **4,963 B MP3（ID3 魔数）**（5/5）。
  证据 `cut/results/wyoming-results.json`、`cut/results/voice-results.json`。
  **踩坑记录**：`platform` 参数是旧名，会报 `Provider tts not found`；`engine_id` 才是当前正确入参。
- [ ] **CONNECT-01b｜真实语音闭环（麦克风/音箱/STT）。** 仍未做：TTS 是生成音不是语音模型；无麦克风、无音箱、无 STT，因此"说话→识别→意图→设备→播报"整环未验证。
- [x] **CONNECT-02a｜摄像头链路（合成源）。** generic 配置流→预览确认→条目创建→`camera.127_0_0_1` 实体→`/api/camera_proxy` 取回 5429 B JPEG 快照。证据 `cut/results/camera-results.json`（5/5）。
- [x] **CONNECT-02b1｜RTSP→stream→HLS 管线（真实视频编码）。** 用 ffmpeg 生成 H264 测试图案（640x360@15fps）推给本地 mediamtx RTSP 服务器，HA 用 generic camera 的 `stream_source` 接入；HA 实际读取 RTSP 会话 63 秒（mediamtx 日志 `is reading from path cut`），前端经 WebSocket `camera/stream` 取得 HLS 地址，master → variant → `init.mp4`（804 B）→ 媒体分片 `segment/7.0.m4s`（**143,177 B**）全部 200。证据 `cut/results/rtsp-results.json`（5/5）。
  **两个真坑（已记入 REPRODUCE）**：① HLS 用的 token 由 WebSocket `camera/stream` 下发，与实体属性里的 `access_token` **不同**，用后者必然 404；② HLS 响应是 gzip，不先解压会把 m3u8 当二进制。
- [x] **CONNECT-02b2a｜断流与恢复（本轮做成，仍非真实摄像头）。** 掐掉 RTSP 推流进程 20 秒再重启，
  判据用 mediamtx 侧 `is reading from path` 会话计数 + 分片新鲜度 + 实体状态：读会话 **6 → 7（新增 1）**、
  重新拉出**新分片 `segment/0.m4s` 932,281 B**、摄像头回到 `idle` => `recovered`。证据 `cut/results/stream-recovery.json`。
  **防自欺教训**：第一版探针把"重启后分片字节数与中断前相同（142,022）"当作恢复，而实体状态其实是
  `unavailable`——那是缓存分片。恢复类判据必须同时看 ①broker 是否新增读会话 ②分片是否新生成
  ③实体是否回到 `idle/streaming`，不能只比字节数。
- [x] **CONNECT-03a｜MQTT broker＋模拟设备全链。** 配置流建条目→retained 发现生成实体→状态订阅→服务调用下发 `OFF`→broker 重启后重订阅仍工作。证据 `cut/results/mqtt-results.json`（9/9）。
- [x] **CONNECT-03b1｜MQTT over TLS + QoS2 + 断线重连（本轮做成，不需要硬件）。**
  **TLS（6/6）**：自签 CA + 带 `keyUsage`/`SAN` 的服务端证书，mosquitto 2.0.21 起 8883 TLS 监听；
  HA 配置流用 `other_settings.set_ca_cert=custom` **上传 CA 文件**建条目 → `state=loaded` →
  在 TLS 通道上实测收到 `over-tls`。证据 `cut/results/mqtt-tls-final.json`。
  **QoS2**：HA 以 qos=2 发布、外部订阅端以 qos=2 订阅，实测 `granted_qos=2`。
  **断线重连**：SIGTERM 杀 broker → 重启 → HA 的 mqtt 条目回到 `loaded`，重启后收发正常。
  **两个真坑**：① `mqtt` 是 `single_config_entry`，加第二个条目会 `Invalid flow specified`，只能替换；
  ② `set_ca_cert=auto` 要求 CA 在 Python 信任库里，本容器 `update-ca-certificates` 返回 rc=0 却
  `0 added`（bundle 里根本没有），且 `tls_insecure=true` 也救不回 `cannot_connect`——
  必须走"上传 CA 的 custom 模式"（`/api/file_upload` 字段名 `file`、返回 `file_id`）。
- [x] **CONNECT-03b2a｜mTLS + 弱网 + 转接式「真设备」（本轮做成）。**
  **① mTLS 强制已核验**：独立 broker（podman 桥 `10.88.0.67:8883`，原生 amd64）设
  `require_certificate true` + `use_identity_as_username true`。双向判据：
  **不带客户端证书 → `TLSV13_ALERT_CERTIFICATE_REQUIRED` 被拒**；**带证书 → TLSv1.3 握手成功并收到
  MQTT CONNACK `0x20`**。HA 侧用配置流 `set_client_cert=true` 上传证书/私钥、`set_ca_cert=custom`
  上传 CA（`/api/file_upload` 字段名 `file`、返回 `file_id`），条目 `loaded`，mTLS 通道上 QoS2 实测
  收到 `over-mtls`（**5/5**）。证据 `cut/results/mqtt-mtls-ha.json`。
  **② 转接式设备（板端零改动）**：`cut/board_mqtt_adapter.py` 是外部只读桥，只对板端现有端点发 **GET**，
  把状态重新发布成 MQTT 设备——设备身份（identifiers/manufacturer/model/sw_version）、**MQTT 自动发现**
  （8 个 retained config 主题）、**LWT 遗嘱可用性**、retained 状态、以及「新录像出现」事件实体。
  适配器持有**自己的客户端证书**（`CN=board-113-adapter`），与 HA 的证书互不相同。**9/9 通过**：
  7 个传感器 + 1 个事件实体被自动发现；设备注册表出现 `WCIS Board 113`（manufacturer=WCIS、
  sw_version=vision-20261103）；HA 侧值等于板端真值（recorder_mode=off、captured_frames=116717、
  recordings=20）；**SIGKILL 适配器后实体变 `unavailable`（LWT 生效）**。证据
  `cut/results/board-mqtt-device.json`。
  **③ 弱网（netem）**：broker 容器 egress 加 `delay 120ms 30ms loss 5%`。三段实测
  **基线 0.8 ms → 劣化 171.1 ms（max 504.3 ms）→ 清除后 0.8 ms**；劣化期间 HA 条目始终 `loaded`，
  **broker 在丢包链路下重启后 HA 自动重连并发布成功**（**8/8**）。证据
  `cut/results/mqtt-weaknet-results.json`。
  **边界（与结论同读）**：netem **只整形 broker→客户端 一个方向**（在跑的 HA 容器不重建就拿不到
  `NET_ADMIN`）；丢包随机故样本数浮动；未做 WAN 拥塞、乱序/重复、MTU 与数小时长稳；
  「真设备」指**真板地址 + 真数据 + 真 mTLS 身份**，不是第三方传感器硬件。
- [ ] **CONNECT-03b2b｜真传感器硬件与长稳。** 仍未做：无第三方 MQTT 传感器（温湿度/继电器等）、
  无休眠唤醒周期、无固件怪癖兼容、无跨网段真实 WAN、无数小时 soak。
- [x] **CONNECT-04a｜HA→HA4FPAI控制服务协议桥。** HA `rest_command` POST `/v1/commands`（Bearer）→control 库落 `commands` 行（action=`camera.open_view`，client=`ha-small`）。证据 `cut/results/bridge-results.json`（5/5）。
- [ ] **CONNECT-04b｜板端生产业务与真实动作。** 未做：本机 control 实例非板端部署；命令因视频服务未起而 status=failed，未做人员登记/声纹/录像业务联调。
- [x] **CONNECT-04b1｜板端状态只读接入 HA（本轮做成，零写入）。**
  用 `rest` 平台（纯 GET）把板端三个只读端点接成 9 个传感器：8092 capture（state/session/960x540/
  captured/encoded/frame_age）、8093 recorder（mode/state/source_ready/queue）、8093 recordings（数量）。
  配置 `examples/board-readonly.yaml`（`rest: !include`，文件本体是列表）；探针
  `cut/board_readonly_probe.py` 逐项与板端原始应答交叉核对：**9/9**（连
  `no_valid_frame`/`source_ready=False` 这些真实瞬时状态都逐字一致；录像数 20=20）。
  **只读保证**：实例域清单无 `switch`/`button`；配置里没有任何 POST 到板端；录像模式
  （off/auto/manual）**刻意不做成 switch**——那是写板端 recorder 进程并写 `/mnt/wcis-data` 的动作。
  证据 `cut/results/board-readonly-results.json`；功能回归仍 30/30。
  **一个配置坑**：`!include` 进 `rest:` 的文件必须本身是列表；文件里再写一层 `rest:` 会报
  `'rest' is an invalid option for 'rest'`。
- [x] **CONNECT-05a｜真实浏览器UI（关键页）。** 注入真实会话令牌后：Overview 仪表盘、History 页、Automations 列表与“新建自动化”编辑器（When/And if/Then do/Save）、Integrations 页显示 MQTT/Generic Camera/RESTful Command。证据：会话内截图 ui-01…ui-05（存 workenv/MCP 输出目录）。
- [x] **CONNECT-05b1｜前端创建并保存自动化。** 在真实前端用编辑器（含 YAML 模式）创建自动化并保存：落盘到
  `automations.yaml`（id `1794021359526`），实体 `automation.ui_created_cut_automation` 加载为 on，
  状态触发后 `last_triggered` 更新（真触发，非草稿）。证据 `cut/results/ui-automation-results.json`（3/3）。
  **顺带修掉一个真缺陷**：示例配置原先用内联 `automation:` 块，导致 UI 保存的 `automations.yaml` 从不被加载、
  界面上"保存成功"却不产生实体；已把 `automation`/`script`/`scene` 改为 `!include` 拆文件（与官方默认一致），
  改后 30/30 功能回归通过。
- [x] **CONNECT-05b2a｜Lovelace 仪表盘编辑与持久化。** 真实前端点「Edit overview」→ 关闭 Lights 汇总 → Save；落盘证据：`/runtime/.storage/frontend.system_data` 出现 `{"type":"summary","key":"light","hidden":true}`，刷新后 Overview 的 Lights 汇总消失。截图 `cut/results/ui/ui-09-lovelace-edit.png`、`ui-10-profile.png`。
- [x] **CONNECT-05b2b｜退出登录。** 在 `/profile` 点 Log out，前端退回未认证态（显示 Loading 并重走授权流程）。**边界**：退出只清浏览器本地会话，API 令牌（长期令牌/refresh token）仍有效——这是 HA 设计，不是缺陷；没有做令牌吊销测试。
- [x] **CONNECT-05b2c｜摄像头卡片与浏览器播放（本轮做成）。** 策略型 Overview 没有「添加卡片」入口，
  改用 Lovelace WebSocket API 建 **storage 模式**仪表盘 `ha-small-cut`（4/4：列表/创建/存配置/回读），
  放 `picture-entity` + `glance` 两张卡；浏览器实测：
  卡片渲染出 `hui-picture-entity-card`，图片来自 `/api/camera_proxy/...?authSig=`（**560×420**）；
  点开卡片进入 `ha-camera-stream`，`<video>` **readyState=4、640×360（与推流源分辨率一致）**，
  4 秒内 `currentTime` 从 29.359 → 33.374（推进 4.015 s，`paused=false`）⇒ 画面在按实时推进，不是停在首帧。
  证据 `cut/results/camera-card.json`、截图 `cut/results/ui/ui-11-camera-card.png`、`ui-12-camera-playing.png`。
- [x] **CONNECT-02b2b｜真实摄像头（板端 SDI→PL→PS→VPU，本轮做成）。**
  **不再是合成源**：板 192.168.1.113 上真实摄像头经 SDI→PL→PS→VPU(H.264) 出流，服务端
  `vpu_live_server.py`(8092) + `serve_photos.py`(8091) 提供。查清的真实接口：
  `8092/api/status` 给 session/ready；**`8091/api/camera/h264/live.mp4?session=<s>` 是活流**
  （不带 session 一律 503 "Camera session changed"）；`8092/h264/{init.mp4,seg-NNNNNNNNN.m4s}` 给分片。
  **板端故意不暴露 m3u8**（`h264_media.py` 只白名单 init.mp4 与 seg-*.m4s，playlist 路由回 503）。
  做法：本地只读桥接器 `cut/board_hls_bridge.py` 用 ffmpeg 把 live.mp4 重新封装成滚动 HLS
  （6×1s fMP4 窗口）+ JPEG 快照，喂给 HA generic camera（`stream_source` + `still_image_url`）。
  **验证**：接入 7/7；端到端 8/8（HA 分片 52–93 KB、板端采集计数 73801→73860 持续推进、
  session 未变）；浏览器 `<video>` **960×540、readyState=4、5 秒推进 5.01 秒、未暂停**；
  截图 `cut/results/ui/ui-14-board-camera-playing.png` 是**真实办公场景画面**（非测试图案）。
  证据 `cut/results/board-camera-results.json`、`cut/results/board-stream-e2e.json`。
  **两个坑**：① 桥接器的 m3u8 必须原样透传 ffmpeg 的滚动窗口（含 `EXT-X-MEDIA-SEQUENCE`），
  自己拼静态窗口会被 HA 判成 "Stream ended; no additional packets"；② 旧的桥接/ffmpeg 残留进程
  会占住板端预览槽位，导致分片停更，重启前必须先清干净。
  **边界**：HA 跑在本机（板端只有 Python 3.8，装不了 HA 3.14 基线）；桥接器只读，不开采集实例、
  不碰 SDK/DMA/VPU；端到端延迟与弱网未量化。
- [ ] **CONNECT-02b2c｜播放质量与多路并发。** 未做：端到端延迟、丢帧率、多客户端并发上限未测。

## 五、进一步裁剪与上板条件

- [x] **NEXT-01a｜体积构成分析。** 分支树跟踪源码 **38.4 MB**（6173 文件）；实测安装 `/venv` **784 MB**，其中 site-packages 748 MB：`hass_frontend` 268 MB、`av.libs` 100 MB、`homeassistant` 72 MB。结论：**继续删源码对安装体积影响很小**，瓶颈是前端包与 av 库。证据 `cut/results/size-results.json`。
- [x] **NEXT-01b｜继续收缩可选集成（三轮修正后：129 → 128，只有 1 个可删）。**
  v1（129→119）漏了跨行括号导入；v2（129→120）漏了 **manifest 硬依赖**（`frontend` 的 `dependencies` 里有
  `map_tiles`/`search`），且验证脚本只匹配 `ERROR`/`Invalid config`，漏掉小写 `Integration error:`——两次都被推翻。
  **v3 规则**：manifest `dependencies`（硬）＋跨行感知的静态导入＋bootstrap 预导入，三条同时满足才可删；
  129 基线上算出**只有 `plex` 一个**满足。
  **验证（全新 venv）**：删 `plex` → 128 集成 → `pip install` rc=0、`check_config` exit=0、
  严格判据（`Integration error`/`not found`/`ImportError`/`Invalid config`/`Setup failed`/`Traceback`）**零命中**、
  `pip check` 无冲突、**30/30 功能回归通过**。已采纳。
  证据 `cut/results/next01-imports-v3.json`、`cut/results/plex-results.json`、`cut/results/trial-functional-8125.json`。
- [x] **NEXT-02a｜体积统计完成。** 见上；同时确认前端资产为哈希 chunk（未删除任何被引用 chunk），资产级裁剪未做。
- [x] **NEXT-02b｜前端资产构成分析（结论：不裁源码树内资产）。** hass_frontend 232.8 MB / 14682 文件：static 99.2、translations 69.3、frontend_es5 68.6、frontend_latest 64.1、locale-data 21.3；其中 `.map` 51.6 MB、`.gz/.br` 52.7 MB 理论上可在自建打包时剔除，属重打包上游前端包，未做。证据 `cut/results/frontend-results.json`。
- [x] **NEXT-03a｜ARM64 轮子盘点（两轮，修正口径）。** 首轮只试 `manylinux2014_aarch64/cp314`，报 11 个缺；改用真实 pip 解析的标签组合（`manylinux_2_28/2_17/2014_aarch64` × `cp314/abi3`）复检后**只有 5 个缺轮**：`pymicro_vad`、`pyspeex_noise`、`PyTurboJPEG`、`PyQRCode`、`PyRIC`。首轮把 numpy/pillow/greenlet/pycares/ulid-transform 误报为缺轮，原因是标签过窄——这条更正已留档。证据 `cut/results/arm64-results-v2.json`。
- [x] **NEXT-03b｜两个含 C++ 扩展的包（真 aarch64 轮，本轮做成）。**
  **v1 结论作废**（`_PYTHON_HOST_PLATFORM` 只改文件名，轮内 `.so` 仍是 x86_64，ELF machine=62）。
  **本轮正确做法**：在 aarch64 容器里原生构建 —— 先把纯 Python 的 `setuptools 84.2.1`/`wheel 0.48.0`
  从 x86 venv 跨架构拷入，apt 强制 IPv4 装上 `g++/cmake/make/libspeexdsp-dev`，再
  `pip wheel --no-build-isolation --no-binary :all:` 构建：
  `pymicro_vad-1.0.1-cp314-cp314-linux_aarch64.whl`、`pyspeex_noise-1.0.2-cp314-cp314-linux_aarch64.whl`。
  **判据**：轮内 `micro_vad_cpp.cpython-314-aarch64-linux-gnu.so`、`speex_noise_cpp...aarch64...so`，
  ELF `machine=183`（EM_AARCH64）；安装后 `import pymicro_vad, pyspeex_noise, micro_vad_cpp, speex_noise_cpp` 全通过，
  `pip check` 无冲突。**`PyQRCode`/`PyRIC`/`PyTurboJPEG` 本就是纯 Python，无需构建。**
  证据：`cut/results/arm64-native-wheels.json`。
- [x] **NEXT-03c｜ARM64 上真正安装并启动（本轮做成，模拟环境）。**
  在 podman machine 内挂载并注册 `qemu-aarch64-static` 到 binfmt_misc 后，得到真实 aarch64 容器
  （`uname -m`=aarch64、Python 3.14.10、SQLite 3.46.1）。129 树装入后：
  `pip check` 无冲突；`hass --script check_config` **exit=0 且零 "Integration error/not found/ImportError"**；
  完整启动 **前端 HTTP 200、`/api/` 401（认证正常）、无 ImportError、无 Setup failed**。
  证据 `cut/results/arm64-runtime.json`。
  **边界（必须保留）**：这是 x86 宿主上的 **qemu 用户态模拟**，不是目标板 113；未测板端内存/长稳/并发，
  也不代表性能可比。
- [ ] **NEXT-04｜板端并发内存与稳定性。** 未做：本机 x86 读数不能推算板端；需在板端隔离部署测启动峰值/长稳/回退。
- [x] **NEXT-05a｜可复现源码包与wheel构建。** `git archive` 源码包 6,135,211 B（sha256 `a3d48538…`），包内 `verify_source.py` 通过（5909/119/0），`pip wheel --no-deps` 出 `homeassistant-2026.10.4-py3-none-any.whl` 8,131,064 B（sha256 `6176c274…`）。详见 `cut/BUILD.md`。
- [x] **NEXT-05b1｜deb 打包与离线安装实测（本轮做成）。** `dpkg-deb` 打出 **`ha-small_0.1.0_all.deb`
  （7,366,076 B / `c56af67a74a4507f26daa04659009993bf901e3aac31fbec4da802d809c841a8`）**，
  内含 wheel + `requirements-voice.txt` + `/etc/ha-small`（conffiles）+ `/usr/bin/ha-small`
  （未 setup 时给出明确提示）+ `ha-small-setup`（自动挑 Python>=3.14，支持 `HA_SMALL_WHEELHOUSE` 离线装）
  + systemd unit（ExecStartPre 检查 venv 存在）。
  **实测（干净 Debian 13 x86_64 容器）**：`dpkg -i --force-depends` → `ha-small-setup` 从 wheelhouse
  **离线**安装（`--no-index`，103 个 wheel）→ `pip check` 无冲突 → 启动 **前端 200 / `/api/` 401 /
  零 ImportError**，两个语音原生扩展（x86 预编译轮）导入 OK。证据 `cut/results/deb-results.json`；
  构建脚本在 `workenv/ha-small/build_deb.sh`，deb 本体留 workenv 不入库。
  **三个坑**：① 离线装若 wheelhouse 缺 `setuptools`+`wheel`，`PyRIC`（唯一 sdist）会在构建隔离里失败；
  ② 只装 wheel 不含 `pymicro-vad`/`pyspeex-noise` 时 `assist_pipeline` 会 Setup failed（它们是 HA 运行时按需装的），
  已做进 `requirements-voice.txt` 并把预编译轮放进 wheelhouse；③ 旧实例不杀会被单实例锁拒绝，
  表现为前端 404 的假象。
- [x] **NEXT-05b2a｜aarch64 依赖供给与 aarch64 上的 deb 离线安装（本轮做成）。**
  用「从 x86 wheelhouse 读精确版本、逐个 `--no-deps --platform manylinux_2_28_aarch64` 下载」的办法
  绕开 pip 跨平台解析的 `resolution-too-deep`：**102/104** 个包拿到 aarch64 轮，缺的恰好是我们自己
  原生编译的那 2 个（`pymicro-vad`/`pyspeex-noise`，已有 EM_AARCH64 轮）；再补 `PyRIC`（唯一 sdist，纯 Python）
  共 **105 件**。在这套 wheelhouse 上，同一个 `all` 架构 deb 在**真 aarch64 容器**里：
  `dpkg -i` → `ha-small-setup` 离线安装 → `pip check` 无冲突 → 语音原生扩展导入 OK → 启动前端 200。
  证据 `cut/results/deb-aarch64-results.json`、`cut/results/deb-functional-aarch64.json`。
  **aarch64 上 30 项回归是 28/30，两项失败已定位为模拟层限制**：`zeroconf` 调
  `setsockopt(IPPROTO_IPV6, IPV6_MULTICAST_IF)` 在 **qemu 用户态模拟**下返回 `ENOPROTOOPT`
  （同一段代码在原生 x86_64 上 `ok`），导致 aiohttp 解析器建不起来 → `rest_command` 失败 →
  `not-recovery-mode` 与 `external-http-control` 双双失败。真 ARM64 硬件不会走这条模拟路径。
  另记：该实例后来被 HA 自己的「HTTP 配置确认超时」重启机制停掉（`Pending HTTP config was not
  confirmed within 0:05:00`），不是打包缺陷。
- [ ] **NEXT-05b2b｜真板部署 / Actions / Release。** 未做：未在目标板 113 上装（板端只有 Python 3.8，
  本基线要求 >=3.14.2）；未配 Actions（本项目规定手动触发）；未发 Release（分支 `ha-small` 本身已于
  2026-11-07 获授权推送）。
  **另一条事实**：现行发行版都没有 python3.14 的 dpkg 包，`Depends: python3.14 | python3 (>= 3.14)`
  无法被 apt 满足——部署前提是先有 Python 3.14 环境。

## 六、这一轮没有采用的做法

- 不拷贝HA4FPAI的自研control目录冒充“裁剪HA”。
- 不把旧HA2021强行装到板子后声称具备现代Assist能力。
- 不删除Python版本检查、权限或真实错误以换取启动成功。
- 不让“保留代码”“加载框架”“真实设备闭环”共享一个完成勾。
- 不把源码缩减比例当成依赖、RSS、启动峰值同比缩减。

## 七、按依赖串行执行分组

用户要求：使用limit分组中的group/group3执行；子agent串行、不并发。每组执行结束后由主代理核对原始证据，再更新原任务ID的复选框；分组本身不新增“已完成产品功能”。

| 顺序 | 分组与原任务ID | 具体工作 | 验收及依赖 |
|---|---|---|---|
| G1 | 删除与体积优化：NEXT-01、NEXT-02 | 先统计实际安装/前端/依赖体积和导入路径，验证哪些可继续只删除；保留主要功能，必要补丁单列 | 保留文件哈希审计；重跑30功能＋4重启；分别报告源码/完整环境/RSS；不能以删掉功能换体积 |
| G2 | 功能接入：CONNECT-01至CONNECT-05 | 在隔离环境依次验证语音提供者、摄像头/媒体、MQTT、外部业务桥、真实浏览器UI；每次只派一个子任务 | 使用具体服务或设备证明闭环；stub/模拟通过单独记，不冒充真实硬件；缺设备的项继续留空；不碰HA4FPAI/npuserv |
| G3 | ARM64与交付：NEXT-03、NEXT-05 | 核对独立ARM64 Python>=3.14.2、轮子/原生依赖和安装路径，再准备可复现包 | 先在允许的构建机处理依赖，不在板上编译；打包要有版本、许可、哈希与安装验证；依赖G1稳定裁剪集与G2相关功能合同 |
| G4 | 目标板预算与稳定性：NEXT-04 | 在不替换现役服务的隔离部署中测峰值、并发资源、启动/退出/回退与长稳 | 依赖G3 ARM64可运行产物；现场资源证据才能勾，不用x86读数推算板端 |

### 当前路由阻断记录

2026-11-06已做只读派发探测，未对产品源码做进一步裁剪、安装或部署。结果：provider=limit返回`no adapter registered for provider "limit"`；沿用当前magpie适配器并指定model=group/group3返回`pi-ai provider "magpie" has no configured model "group/group3"`，group/group-3拼写也未配置。把group当provider同样无适配器。

四组均为**待执行**；没有成功运行的group3执行代理。需要先提供本环境已配置的准确provider/model，或在Harness启用指定路由；不可将默认模型的输出标作group3成果。路由恢复后从G1开始，每组只启动一个执行代理。

## 八、2026-11-07 本机执行记录（G1/G2 可做部分）

环境：本机 Podman 容器 `ha-small-dev`（Linux x86_64，Python 3.14.10，SQLite 3.46.1），**用分支树本身**安装（`pip install --no-deps --force-reinstall /src-branch`），测试目录 `/runtime`（带 `HA_SMALL_TEST_ONLY` 标记，测试账户与随机口令，不记录明文）。

| 项 | 结果 | 证据 |
|---|---|---|
| MQTT 全链（配置流→发现→状态→控制→broker重启重订阅） | 9/9 通过 | `cut/results/mqtt-results.json` |
| 摄像头（generic 合成 JPEG→实体→代理快照 5429 B） | 5/5 通过 | `cut/results/camera-results.json` |
| 业务桥（HA rest_command→control `/v1/commands`→库中命令行） | 5/5 通过 | `cut/results/bridge-results.json` |
| 浏览器 UI（Overview/History/Automations 编辑器/Integrations 列表） | 通过（页面级） | 会话截图 ui-01…ui-05 |
| 体积统计 | 源码 38.4 MB；安装 784 MB（前端 268 MB、av 100 MB、HA 72 MB） | `cut/results/size-results.json` |
| ARM64 轮子盘点（复检） | 112/117 有轮，5 缺 | `cut/results/arm64-results-v2.json` |

| 试删集成（三轮定稿） | v3 规则下仅 `plex` 可删，已采纳 129→128（干净venv+30/30） | `cut/results/plex-results.json` |
| 前端创建并保存自动化（含 workspace 陷阱修复） | 3/3：落 automations.yaml、实体加载、真触发 | `cut/results/ui-automation-results.json` |
| aarch64 deb 离线安装 | 105 轮 wheelhouse；pip check 干净；前端 200；30 项 28/30（两项为 qemu 限制） | `cut/results/deb-aarch64-results.json` |
| deb 打包与离线安装 | 7,366,076 B；dpkg 装→wheelhouse 离线 setup→前端 200 | `cut/results/deb-results.json` |
| 板端真实摄像头接入 HA | 7/7 接入 + 8/8 端到端；960x540 浏览器实时播放 | `cut/results/board-stream-e2e.json` |
| 浏览器播放（摄像头卡） | storage 仪表盘 4/4；video readyState=4、640x360、4 s 推进 4.015 s | `cut/results/camera-card.json` |
| 断流恢复（视频） | 读会话 6→7、新分片 932,281 B、状态回 idle | `cut/results/stream-recovery.json` |
| RTSP→stream→HLS 管线 | 5/5：H264 测试流→HA→master/variant/init.mp4/分片 143,177 B | `cut/results/rtsp-results.json` |
| MQTT mTLS + 转接设备 + netem | mTLS 拒无证书/收证书 5/5；设备 9/9（LWT 生效）；8/8（0.8→171.1→0.8 ms） | `cut/results/mqtt-weaknet-results.json` |
| MQTT TLS + QoS2 + 重连 | 6/6：CA 校验 TLS 通道实测收发、granted_qos=2、重启恢复 | `cut/results/mqtt-tls-final.json` |
| plex 单点裁剪 | 干净 venv：check_config 零命中 + 30/30 回归 → 采纳 129→128 | `cut/results/plex-results.json` |
| Wyoming TTS 语音链路 | 5/5 + 5/5：协议 26,460 B PCM → HA tts 实体 → 4,963 B MP3 | `cut/results/voice-results.json` |
| ARM64 安装+校验+启动 | aarch64：install OK、check_config 零错、前端 200、API 401 | `cut/results/arm64-runtime.json` |
| ARM64 原生轮（2 个 C++ 包） | EM_AARCH64 已核验，导入通过 | `cut/results/arm64-native-wheels.json` |
| 前端包构成 | 232.8 MB：static 99.2 / translations 69.3 / es5 68.6 / latest 64.1；.map 51.6、.gz+.br 52.7 | `cut/results/frontend-results.json` |

过程中确认的三个实现细节（供复跑）：MQTT 在现代 HA 是 config-entry（YAML `broker/port` 会被判非法），流提交需 `protocol`（3.1/3.1.1/5 之一）且 `other_settings.set_ca_cert` 用 `off`；generic 摄像头配置流在 `user_confirm` 步要 `confirmed_ok=true`；摄像头取图走 `/api/camera_proxy/<entity>?token=…`（`/api/camera/snapshot` 在本版本 404）。`rest_command` 的 payload 若含 `{{ "x": 1 }}` 会被 Jinja 误判，需写单层花括号。

**仍搁置的项与原因（2026-11-07 复核）**：

| 项 | 为什么搁置 | 已做到的部分 |
|---|---|---|
| CONNECT-01b 真实语音闭环 | 容器无声卡、无麦克风/音箱、无 STT 模型 | Wyoming TTS 协议 5/5 + HA tts 链路 5/5 |
| CONNECT-02b2c 播放质量与并发 | 延迟/丢帧/并发未量化 | 板端真实摄像头已接入并在浏览器播放 |
| CONNECT-03b2b 真传感器与长稳 | 无第三方硬件、无真实 WAN、无 soak | mTLS 强制已验、转接设备 9/9（含 LWT）、netem 弱网 8/8 |
| CONNECT-04b2 录像模式/登记写入 | 写板端 recorder 与 gallery，需授权与时机 （quota 4 GiB 不自动删；现役压缩任务在跑） | 只读 9/9：capture/recorder/recordings 传感器逐项对上 |
| CONNECT-05b2d 播放质量量化 | 未测端到端延迟/丢帧/弱网抖动 | storage 仪表盘 4/4 + 浏览器播放实时推进（4 s 走 4.015 s） |
| NEXT-02b 前端资产裁剪 | `.map` 51.6 MB / `.gz`+`.br` 52.7 MB 需重打包上游 wheel | 资产构成已盘点 |
| NEXT-04 板端资源与长稳 | ARM64 是 qemu 用户态模拟，非目标板 113 | aarch64 安装/校验/启动通过 |
| NEXT-05b2b 真板/Actions/Release | 板端只有 Python 3.8；Actions 手动；发布需授权 | aarch64 wheelhouse(105)+deb 离线安装→启动已实测 |


## 九、清单维护

做完一项再勾，写明输入、环境、命令、结果和局限。功能探针必须在带测试标记的独立配置目录运行。发现被裁掉的上游依赖时，优先恢复最小必需集合再重新审核哈希；确实只能修改时才登记PATCHES，并重新跑受影响功能。
