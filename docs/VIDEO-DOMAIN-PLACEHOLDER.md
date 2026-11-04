# 视频域留空清单与接回方案

> 状态：**有意留空**（2026-11-05，她决定：「视频链路先别把 yuanma/ 那边搬过来，先留空，并记录留空了哪些部分怎么接」）。
> 本页是 `unified-control` 分支上视频域留空部分的**唯一登记处**：留了什么、为什么、接回时怎么接、按什么验收。
> 规划正本：`../../wiki/mainchain/HA4FPAI与视频联合总规划.md`、`../../wiki/mainchain/联合控制接口与视频接入.md`。

## 一、留空原则

1. **不搬源码**。视频实现（采集 owner、FAC1、识别服务、录像服务、特征历史模块）继续住在
   `repos/FPAI30TAI图像FPGA/`（含 `yuanma/submission` 交付快照）。本分支零拷贝，避免双正本。
2. **不假成功**。所有 `camera.*`／`recording.*` 动作执行时返回
   `video_domain_not_connected`（HTTP 501），绝不返回编造的识别结果或「已开始录制」。
3. **不越所有权**。摄像头／iCraft SDK／DMA／VPU 由视频侧单 owner 进程独占；接回后本服务仍只经
   HTTP 域接口调用，不新开 SDK／采集实例（规划 §模块与写入所有权）。
4. **能力先声明**。动作注册表已包含全部视频动作（参数、危险级别、能力归属），前端／语音映射
   可以先建；执行端接回时只换适配器实现，不动契约。

## 二、留空清单与逐项接法

| # | 留空项 | 代码落点（现在） | 接回方式（怎么接） | 关联验收 |
|---|---|---|---|---|
| V1 | 摄像头单帧识别 | `control/adapters/video.py::recognize`（桩） | 实现 `recognize`：POST `8091 /api/recognize-camera`，把回包映射为 `{frame_id, frame_token, identities[], pad{status, security_calibrated}, ages}`；命令结果带 `frame_token`，过期（`stale`）结果不用于任何授权 | 验收10/14 |
| V2 | 拍照存档 | `video.py::snapshot` | GET `8091 /api/camera/preview.jpg` → 写受控媒体目录（tmp→fsync→rename，见规划 §文件与数据库的一致性）→ 事务写 `media_assets` → 回 `media_id`；编码为 H.264 时该口可能 503，如实转发错误 | 验收08/10 |
| V3 | 实时画面打开 | `video.py::open_view` | 返回 `8092 /h264/manifest.json` 系播放地址；「已看到画面」以客户端回执为准（`camera.open_view` 结果只代表地址可用，不播假成功）；编码切换时播放器按新 session 重建 | 验收10 |
| V4 | 录像模式控制 | `video.py::set_mode` | POST `8093 /api/mode`（同源校验：由控制服务在板端本机调用，或经同源代理）→ 回读 `mode` → `settings.apply_feedback('recorder', epoch, revision)`；`armed`（已设模式）与 `actively_recording`（确有新鲜帧）分开报 | 验收09/10/11 |
| V5 | 录像列表与回放 | `video.py::list_recordings` / `play` | GET `8093 /api/recordings`（分页）映射进业务库 `recordings` 目录（保留 `trash_state` 语义）；回放按 `recording_id` 取 `8093` MP4 Range 地址；MJPEG offset 索引与 H.264 采集时间索引不是同一种寻址，`index_format` 原样透传 | 验收10 |
| V6 | 录像/媒体目录写入口 | `recordings`／`media_operations` 表（已建，无写入方） | 接回时由视频侧 recorder 适配器经 outbox 协议提交：operation_id → 媒体封段完成回执 → 业务库目录行；回收站/`purging` 状态机按规划 §录像迁移不变量保留 | 验收08/09 |
| V7 | 人脸登记执行端 | `person.enroll_face`（只收显式向量） | 接回：调 `8091 /api/enroll` 或本地提特征 → 模型/质量证据齐全后经 `samples.enroll` 入业务库；真实提取端在视频侧，本服务不复制推理代码 | 验收03 |
| V8 | 特征历史库（独立 SQLite） | 未建（视频侧模块持有） | 历史库继续由视频提取模块写入；与本库只按 `event_id`／frame 标识对账，不做伪外键；历史检测永不自动升级为登记身份 | 验收02 |
| V9 | 媒体上传/读取端点 | `POST /v1/uploads`、`GET /v1/media/{id}`（未实现） | 接回时实现受控上传（类型/大小/时长上限）与按 `media_assets` 白名单读取；不做任意路径代理 | 验收11 |
| V10 | 任务 worker 常驻循环 | `control/tasks.py::run_due`（幂等，可手工触发） | 部署层起独立 worker（或 systemd timer）周期调 `run_due`；worker 必须带 lease 与 fencing（restore_epoch），旧世代不得执行 | 验收12/13 |

## 三、接回顺序（对应实施路线工作包）

1. **WP5**：V4/V5/V6（录像域）——只读列表与模式控制先行，回执协议落地。
2. **WP8**：V1/V2/V3（识别与预览）——接统一命令管线，动作结果进事件与页面。
3. **WP11**：V7/V8/V9（登记执行端、历史库对账、媒体端点）。
4. **WP12**：整机并行长稳（验收15）前完成全部接回并跑 验收08–11。

每接回一项：删掉对应桩函数体 → 按上表实现 → 在本页该行标记「已接回 + 提交号」→
跑 `control/tests/smoke.py` 与对应验收项。**留空清单清零前，`/v1/capabilities` 的
`video_domain.connected` 保持 false，界面不得显示视频能力为可用。**

## 四、现状边界（不要从本页推出的话）

- 本分支**未部署**：板上跑的是已发布 0.1.1（八级语音链）+ 现役视频服务，与本骨架无关。
- `8091/8092/8093` 端点备忘来自交付文档与源码核对（`repos/FPAI30TAI图像FPGA/docs/api-index.md`），
  接回前必须按现场服务核对；本页不承诺这些服务当前在线。
- klar 注册 JSON（`make_registry`）的实体种子尚未导入业务库（WP4/7 范围），当前导出为空表属预期。
