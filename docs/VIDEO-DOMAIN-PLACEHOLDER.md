# 视频域留空清单与接回方案

> 当前状态入口（2026-11-06）：[重构总清单](REFACTOR-CHECKLIST.md)维护实施复选框，[目标架构](REFACTOR-ARCHITECTURE.md)第4节维护指定视频源码复用边界。下文是原接回合同与历史说明，含过时的“未提交/未连板”措辞，不作为当前完成声明。
> 已核对的视频参考为`repos/fmql30tai-sdi-npu-face-recognition`（`c083ec7b5f85af387ddfe446257ef4e31ef54794`，2026-11-08 由`f9e34bc`更新；新增两笔只动`models/`原始权重与许可、README提示，`源码/ps_source/`整棵树哈希`5d05d9cb1f498b03c4de16a9553edfdaa13f5f7b`两版逐字节相同，原结论不受影响），**不再以旧FPAI30TAI图像FPGA路径作为现势规划正本**；V8的face_history SQLite与录像生命周期已在该源码中实现，剩余是集成/部署/对账而非从零建设。旧/api/enroll会直接写gallery，不能无修改当成纯向量提取API。
> 模块状态：V1–V5已有HTTP客户端；V6已有HA接收端；V9/V10已有模块。真实域集成、单写者与生产验收分别按总清单勾选，board_verified不得因模拟测试通过翻真。
> 2026-11-05 她决定先留空（「视频链路先别把 yuanma/ 那边搬过来，先留空，并记录留空了哪些部分怎么接」）；
> 同日按本页清单把 V1–V5 接回为真实 HTTP 客户端（未提交、未推送、未连板、未连真实端口）。
> 本页是 `unified-control` 分支上视频域留空部分的**唯一登记处**：留了什么、为什么、接回时怎么接、按什么验收。
> 规划正本：`../../wiki/mainchain/HA4FPAI与视频联合总规划.md`、`../../wiki/mainchain/联合控制接口与视频接入.md`。

## 一、留空原则

1. **不搬源码**。视频实现（采集 owner、FAC1、识别服务、录像服务、特征历史模块）继续住在
   `repos/FPAI30TAI图像FPGA/`（含 `yuanma/submission` 交付快照）。本分支零拷贝，避免双正本。
2. **不假成功**。适配器禁用（`enabled=False`）或未配置目标时，`camera.*`／`recording.*`
   动作返回 `video_domain_not_connected`（HTTP 501）；接通后结果一律来自真实回包：
   字段缺失报 None、回读不一致报 `video_service_error`，绝不返回编造的识别结果或
   「已开始录制」（`actively_recording` 如实为 None）。
3. **不越所有权**。摄像头／iCraft SDK／DMA／VPU 由视频侧单 owner 进程独占；接回后本服务仍只经
   HTTP 域接口调用，不新开 SDK／采集实例（规划 §模块与写入所有权）。
4. **能力先声明**。动作注册表已包含全部视频动作（参数、危险级别、能力归属），前端／语音映射
   可以先建；执行端接回时只换适配器实现，不动契约。

## 二、留空清单与逐项接法

| # | 留空项 | 代码落点（现在） | 接回方式（怎么接） | 关联验收 |
|---|---|---|---|---|
| V1 | 摄像头单帧识别 | `control/adapters/video.py::recognize`（**已实现，板端未验**） | 已实现：POST `8091 /api/recognize-camera`（空请求体），回包映射为 `{frame_id, frame_token, identities[], face_count, pad{…原样}, ages}`，字段缺失一律 None；503→`video_no_frame`（原始 error 透传）、传输失败→`video_unreachable`、非 200/非 JSON→`video_service_error`；命令结果带 `frame_token`，过期（`stale`）结果不用于任何授权 | 验收10/14 |
| V2 | 拍照存档 | `video.py::snapshot`（**已实现，板端未验**） | 已实现：GET `8091 /api/camera/preview.jpg` → `media_root`+store 经 `configure()` 注入后，tmp→fsync→`os.replace`→目录 fsync（平台容忍）落盘 → 事务写 `media_assets`（storage_root / relative_path=`snapshots/<id>.jpg` / kind / sha256 / size_bytes）→ 回 `{media_id, sha256, size_bytes, saved}`；未配 `media_root` 不落盘不写库（saved=False）；编码为 H.264 时该口可能 503，如实转 `video_no_frame` | 验收08/10 |
| V3 | 实时画面打开 | `video.py::open_view`（**已实现，板端未验**） | 已实现：先 GET `8092 /api/status`，`ready` 为真才返回 `8092 /h264/manifest.json` 系播放地址（未就绪→`video_service_error`）；「已看到画面」以客户端回执为准（`camera.open_view` 结果只代表地址可用，不播假成功）；编码切换时播放器按新 session 重建 | 验收10 |
| V4 | 录像模式控制 | `video.py::set_mode`（**已实现，板端未验**） | 已实现：POST `8093 /api/mode` → 回读校验（不一致→`video_service_error`）→ `{mode, armed:True, actively_recording:None}`；urllib 默认不发 Origin，落在服务端同源允许集（None）天然通过，不伪造 Origin 头；`settings.apply_feedback('recorder', …)` 回执接线仍待集成层（actions/service 不在本次改动范围） | 验收09/10/11 |
| V5 | 录像列表与回放 | `video.py::list_recordings` / `play`（**已实现，板端未验**） | 已实现：GET `8093 /api/recordings`（page/limit/from/to/trash）→ 透传 `{status, data}`，结构未知处原样不猜，**入库映射（`recordings` 表）仍留 V6**；`play` 按 `recording_id` 构造 `8093 /api/recordings/<id>/video` 地址，不发请求不取体（Range 由播放端核实）；另 `purge` HTTP 客户端已实现（客户端校验 `confirm==id` 再发，服务端再校验；`trash`/`restore` 未实现，属 V6） | 验收10 |
| V6 | 录像/媒体目录写入口 | **HA 侧接收端已实现**（`control/receipts.py`＋`POST /v1/recordings/receipts`：operation_id 幂等去重、回执不复活 purging/purged、abandon 只记操作） | **视频侧发送端待接**（视频仓工作）：recorder 适配器在封段/软删/清理的持久化节点发回执；segment-complete 需声明文件齐备，业务库只镜像回执不发明状态；trash/restore/purge **动作注册**随发送端一起接（迁移不变量：库行≠媒体提交协议） | 验收08/09 |
| V7 | 人脸登记执行端 | `person.enroll_face`（只收显式向量） | 接回：调 `8091 /api/enroll` 或本地提特征 → 模型/质量证据齐全后经 `samples.enroll` 入业务库；真实提取端在视频侧，本服务不复制推理代码 | 验收03 |
| V8 | 特征历史库（独立 SQLite） | 未建（视频侧模块持有） | 历史库继续由视频提取模块写入；与本库只按 `event_id`／frame 标识对账，不做伪外键；历史检测永不自动升级为登记身份 | 验收02 |
| V9 | 媒体上传/读取端点 | ✅ 已实现（`control/media.py`＋`POST /v1/uploads`＋`GET /v1/media/{id}`：类型白名单/32MiB 上限/原子落盘/media_assets 行/realpath+commonpath 防逃逸；删除态不提供读取） | 已实现；上传限 4 类（jpeg/png/wav/mp4），更大或更多类型按需求扩白名单 | 验收11 |
| V10 | 任务 worker 常驻循环 | ✅ 已实现（`control/worker.py`＋`ha4fpai-task-worker.service`：周期 run_due；restore_epoch 变化即退出(3)交 systemd 重启＝跨世代栅栏） | 部署时 enable 两个单元即可；occurrence 幂等已测 | 验收12/13 |

> **同步说明（2026-11-05，V1–V5 接回时登记）**：`video.status()` 变为
> `{connected: CONFIG.enabled, mode: 'http-client', board_verified: False, targets}`——
> `connected` 语义从「域已接通」改为「适配器已启用」；板端验收前 `board_verified` 恒 False，
> 界面不得把视频能力显示为「已验收/可用」。`control/tests/smoke.py` 里 4 条留空桩时代断言
> （视频状态未接、`video.recognize` 留空桩、`camera.recognize`／`recording.set_mode` 命令码
> `video_domain_not_connected`）按上述新语义已过时，归集成者改写后应恢复全绿；
> 本次按简报未触碰 smoke.py。契约模拟测试（不连真实端口）：
> `python -B -X utf8 control/tests/test_video_adapter.py`。

## 三、接回顺序（对应实施路线工作包）

1. **WP5**：V4/V5/V6（录像域）——只读列表与模式控制先行，回执协议落地。
2. **WP8**：V1/V2/V3（识别与预览）——接统一命令管线，动作结果进事件与页面。
3. **WP11**：V7/V8/V9（登记执行端、历史库对账、媒体端点）。
4. **WP12**：整机并行长稳（验收15）前完成全部接回并跑 验收08–11。

每接回一项：删掉对应桩函数体 → 按上表实现 → 在本页该行标记「已接回 + 提交号」→
跑对应测试与验收项。**V1–V5 本次为工作区改动未提交，提交号由集成者入库时补记。**
**板端验收（验收08–11）通过前 `board_verified` 恒 False，界面不得显示视频能力为
「已验收/可用」**；`/v1/capabilities` 的 `video_domain.connected` 现反映适配器启用态
（见上「同步说明」），动作级 `actions[].connected` 仍由 `actions.py` 注册表如实标（本次未动）。

## 四、现状边界（不要从本页推出的话）

- 本分支**未部署**：板上跑的是已发布 0.1.1（八级语音链）+ 现役视频服务，与本骨架无关。
- V1–V5 已实现为 HTTP 客户端（本页 V1–V5 行），**板端未验收**：`board_verified=False`；
  本机测试一律走契约模拟服务器（`control/tests/test_video_adapter.py`），实现与测试全程
  未连真实 `8091/8092/8093`、未连板。
- `8091/8092/8093` 端点备忘来自交付文档与源码核对（`repos/FPAI30TAI图像FPGA/docs/api-index.md`），
  板端验收前必须按现场服务核对；本页不承诺这些服务当前在线。
- klar 注册 JSON（`make_registry`）的实体种子尚未导入业务库（WP4/7 范围），当前导出为空表属预期。
