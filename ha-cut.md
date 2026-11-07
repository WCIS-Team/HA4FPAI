# ha-cut

更新：2026-11-06。本清单是ha-small的功能与裁剪步骤正本；只要求主要功能，不复制HA4FPAI的46项组件落地方案。已完成步骤当场勾选，未验证的硬件/外部服务保持空框。

## 一、目标与边界

- 归属：HA4FPAI 的 `ha-small` 分支材料；本轮只在本机 `repos/ha-small` 暂存（被 `repos/.gitignore` 忽略），不建远端、不推；分支已按用户授权建立（本地 `1421a9d`，未推）。
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
- [x] **CUT-05｜求依赖闭包。** 遍历manifest依赖/after_dependencies及绝对、相对静态导入，补齐基础实体域；最终119个集成（2026-11-07 又从129试删10个无静态导入的可选集成，30/30功能回归通过），不依靠手工猜61个名字。
- [x] **CUT-06｜执行删除。** 删除1404个集成及对应不保留测试、重新生成的旧发行metadata；全量删除文件清单保存在workenv，裁剪摘要保存在cut/cut-report.json。
- [x] **CUT-07｜证明保留源码未改。** 5909个上游文件有逐文件SHA256；修改数0，补丁清单空。新增说明、例子、锁文件与校验工具独立登记。
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

- [ ] **CONNECT-01｜真实语音输入输出。** 仍未做：需要实际麦克风、音箱与 STT/TTS/Wyoming 提供者；容器无声卡。
- [x] **CONNECT-02a｜摄像头链路（合成源）。** generic 配置流→预览确认→条目创建→`camera.127_0_0_1` 实体→`/api/camera_proxy` 取回 5429 B JPEG 快照。证据 `cut/results/camera-results.json`（5/5）。
- [x] **CONNECT-02b1｜RTSP→stream→HLS 管线（真实视频编码）。** 用 ffmpeg 生成 H264 测试图案（640x360@15fps）推给本地 mediamtx RTSP 服务器，HA 用 generic camera 的 `stream_source` 接入；HA 实际读取 RTSP 会话 63 秒（mediamtx 日志 `is reading from path cut`），前端经 WebSocket `camera/stream` 取得 HLS 地址，master → variant → `init.mp4`（804 B）→ 媒体分片 `segment/7.0.m4s`（**143,177 B**）全部 200。证据 `cut/results/rtsp-results.json`（5/5）。
  **两个真坑（已记入 REPRODUCE）**：① HLS 用的 token 由 WebSocket `camera/stream` 下发，与实体属性里的 `access_token` **不同**，用后者必然 404；② HLS 响应是 gzip，不先解压会把 m3u8 当二进制。
- [ ] **CONNECT-02b2｜真实摄像头硬件与浏览器播放体验。** 未做：源是生成的测试图案而非真实摄像头；未在浏览器里测播放流畅度/拖动/延迟，也未测真实摄像头的鉴权与断流重连。
- [x] **CONNECT-03a｜MQTT broker＋模拟设备全链。** 配置流建条目→retained 发现生成实体→状态订阅→服务调用下发 `OFF`→broker 重启后重订阅仍工作。证据 `cut/results/mqtt-results.json`（9/9）。
- [ ] **CONNECT-03b｜真实MQTT硬件与TLS/QoS2。** 未做：无实际设备，未测 TLS/auth 配置与 QoS2。
- [x] **CONNECT-04a｜HA→HA4FPAI控制服务协议桥。** HA `rest_command` POST `/v1/commands`（Bearer）→control 库落 `commands` 行（action=`camera.open_view`，client=`ha-small`）。证据 `cut/results/bridge-results.json`（5/5）。
- [ ] **CONNECT-04b｜板端生产业务与真实动作。** 未做：本机 control 实例非板端部署；命令因视频服务未起而 status=failed，未做人员登记/声纹/录像业务联调。
- [x] **CONNECT-05a｜真实浏览器UI（关键页）。** 注入真实会话令牌后：Overview 仪表盘、History 页、Automations 列表与“新建自动化”编辑器（When/And if/Then do/Save）、Integrations 页显示 MQTT/Generic Camera/RESTful Command。证据：会话内截图 ui-01…ui-05（存 workenv/MCP 输出目录）。
- [x] **CONNECT-05b1｜前端创建并保存自动化。** 在真实前端用编辑器（含 YAML 模式）创建自动化并保存：落盘到
  `automations.yaml`（id `1794021359526`），实体 `automation.ui_created_cut_automation` 加载为 on，
  状态触发后 `last_triggered` 更新（真触发，非草稿）。证据 `cut/results/ui-automation-results.json`（3/3）。
  **顺带修掉一个真缺陷**：示例配置原先用内联 `automation:` 块，导致 UI 保存的 `automations.yaml` 从不被加载、
  界面上"保存成功"却不产生实体；已把 `automation`/`script`/`scene` 改为 `!include` 拆文件（与官方默认一致），
  改后 30/30 功能回归通过。
- [x] **CONNECT-05b2a｜Lovelace 仪表盘编辑与持久化。** 真实前端点「Edit overview」→ 关闭 Lights 汇总 → Save；落盘证据：`/runtime/.storage/frontend.system_data` 出现 `{"type":"summary","key":"light","hidden":true}`，刷新后 Overview 的 Lights 汇总消失。截图 `cut/results/ui/ui-09-lovelace-edit.png`、`ui-10-profile.png`。
- [x] **CONNECT-05b2b｜退出登录。** 在 `/profile` 点 Log out，前端退回未认证态（显示 Loading 并重走授权流程）。**边界**：退出只清浏览器本地会话，API 令牌（长期令牌/refresh token）仍有效——这是 HA 设计，不是缺陷；没有做令牌吊销测试。
- [ ] **CONNECT-05b2c｜摄像头卡片与播放体验。** 未做：Overview 是策略型仪表盘，无「添加卡片」入口，未在浏览器里验证视频播放/拖动/延迟。管线本身已由 CONNECT-02b1 的 HLS 分片证据覆盖。

## 五、进一步裁剪与上板条件

- [x] **NEXT-01a｜体积构成分析。** 分支树跟踪源码 **38.4 MB**（6173 文件）；实测安装 `/venv` **784 MB**，其中 site-packages 748 MB：`hass_frontend` 268 MB、`av.libs` 100 MB、`homeassistant` 72 MB。结论：**继续删源码对安装体积影响很小**，瓶颈是前端包与 av 库。证据 `cut/results/size-results.json`。
- [x] **NEXT-01b｜继续收缩可选集成（试删验证）。** 静态导入扫描：10 个无导入点（mobile_app/plex/dhcp/ssdp/my/usage_prediction/go2rtc/map_tiles/search/homeassistant_alerts），22 个需改上游（hassio 12 处、blueprint 10、system_health 7、cloud 6…）。已按前者**试删并跑 30/30 功能回归通过**，分支由 129 降至 **119** 集成/5909 文件。证据 `cut/results/next01-imports.json`。
- [x] **NEXT-02a｜体积统计完成。** 见上；同时确认前端资产为哈希 chunk（未删除任何被引用 chunk），资产级裁剪未做。
- [x] **NEXT-02b｜前端资产构成分析（结论：不裁源码树内资产）。** hass_frontend 232.8 MB / 14682 文件：static 99.2、translations 69.3、frontend_es5 68.6、frontend_latest 64.1、locale-data 21.3；其中 `.map` 51.6 MB、`.gz/.br` 52.7 MB 理论上可在自建打包时剔除，属重打包上游前端包，未做。证据 `cut/results/frontend-results.json`。
- [x] **NEXT-03a｜ARM64 轮子盘点（两轮，修正口径）。** 首轮只试 `manylinux2014_aarch64/cp314`，报 11 个缺；改用真实 pip 解析的标签组合（`manylinux_2_28/2_17/2014_aarch64` × `cp314/abi3`）复检后**只有 5 个缺轮**：`pymicro_vad`、`pyspeex_noise`、`PyTurboJPEG`、`PyQRCode`、`PyRIC`。首轮把 numpy/pillow/greenlet/pycares/ulid-transform 误报为缺轮，原因是标签过窄——这条更正已留档。证据 `cut/results/arm64-results-v2.json`。
- [x] **NEXT-03b｜5 个源码包的 aarch64 供给（交叉编译实测）。** 容器内装 `gcc-aarch64-linux-gnu` (14.2.0) 后按源码重建：`pymicro_vad`、`pyspeex_noise` 出 `cp314-cp314-linux_aarch64.whl`（179,801 B / 148,522 B）；`PyQRCode`、`PyRIC`、`PyTurboJPEG` 本身就是纯 Python 轮（`py3-none-any` / `py2.py3-none-any`），不需要交叉编译。**结论：117 个 pin 在 aarch64 上全部有可用产物。**证据 `cut/results/arm64-wheels-results.json`。
- [ ] **NEXT-03c｜ARM64 上真正安装并启动。** 仍未做：本机 podman 无 qemu binfmt（`Exec format error`），板端只有 Python 3.8；**没有在 ARM64 上安装或运行过**，依赖供给已解除不等于跑通。
- [ ] **NEXT-04｜板端并发内存与稳定性。** 未做：本机 x86 读数不能推算板端；需在板端隔离部署测启动峰值/长稳/回退。
- [x] **NEXT-05a｜可复现源码包与wheel构建。** `git archive` 源码包 6,135,211 B（sha256 `a3d48538…`），包内 `verify_source.py` 通过（5909/119/0），`pip wheel --no-deps` 出 `homeassistant-2026.10.4-py3-none-any.whl` 8,131,064 B（sha256 `6176c274…`）。详见 `cut/BUILD.md`。
- [ ] **NEXT-05b｜ARM64/deb/Release交付。** 未做：无 ARM64 包与 deb，未配 Actions，未建 Release，未推送。

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
| ARM64 缺轮交叉编译 | 5/5 有可用产物（2 交叉编译 + 3 纯 Python） | `cut/results/arm64-wheels-results.json` |
| 试删 10 个无静态导入的可选集成 | 30/30 功能回归通过；129→119 集成、6158→5909 文件 | `cut/results/next01-imports.json` |
| 前端创建并保存自动化（含 workspace 陷阱修复） | 3/3：落 automations.yaml、实体加载、真触发 | `cut/results/ui-automation-results.json` |
| RTSP→stream→HLS 管线 | 5/5：H264 测试流→HA→master/variant/init.mp4/分片 143,177 B | `cut/results/rtsp-results.json` |
| 前端包构成 | 232.8 MB：static 99.2 / translations 69.3 / es5 68.6 / latest 64.1；.map 51.6、.gz+.br 52.7 | `cut/results/frontend-results.json` |

过程中确认的三个实现细节（供复跑）：MQTT 在现代 HA 是 config-entry（YAML `broker/port` 会被判非法），流提交需 `protocol`（3.1/3.1.1/5 之一）且 `other_settings.set_ca_cert` 用 `off`；generic 摄像头配置流在 `user_confirm` 步要 `confirmed_ok=true`；摄像头取图走 `/api/camera_proxy/<entity>?token=…`（`/api/camera/snapshot` 在本版本 404）。`rest_command` 的 payload 若含 `{{ "x": 1 }}` 会被 Jinja 误判，需写单层花括号。

**本机不可验证、已搁置的项**：CONNECT-01（无声卡/STT-TTS 设备）、CONNECT-02b（无真实摄像头/RTSP）、CONNECT-03b（无真实 MQTT 硬件、未测 TLS/QoS2）、CONNECT-04b（非板端部署、视频服务未起、未做人员/声纹/录像业务）、CONNECT-05b（未保存自动化/Lovelace 编辑/退出登录）、NEXT-01b/NEXT-02b（补丁级或资产级裁剪）、NEXT-03b（无 ARM64 解释器）、NEXT-04（板端资源）、NEXT-05（打包发布）。

## 九、清单维护

做完一项再勾，写明输入、环境、命令、结果和局限。功能探针必须在带测试标记的独立配置目录运行。发现被裁掉的上游依赖时，优先恢复最小必需集合再重新审核哈希；确实只能修改时才登记PATCHES，并重新跑受影响功能。
