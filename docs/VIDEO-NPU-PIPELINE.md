# 视觉链怎么把模型放上 NPU（现状机制，2026-11-07 读码结论）

> 来源：板上 `/root/wcis-face/releases/20261014-b5a454c11d22/models.json`、
> `apps/vision-20261103/`（serve_photos.py / camera_owner_runtime.py）、
> `/api/health` 自述；源码仓 `repos/fmql30tai-sdi-npu-face-recognition`。

## 一句话

视觉模型**不是**走 npuserv——它有自己的三程序链（C++），模型是 **iCraft 3.39.0 编译的
ZG 件（json+raw）**，由 models.json（sha256 寻址）声明，运行时三个 C++ 进程按角色持有
session；与 npuserv 的关系是 HTTP 客户端（control 侧 video.py），不是共会话。

## 模型件与寻址

- 四颗模型：`yunet`（人脸框+五点）、`mobileface`（身份 512 维）、`minifasnetv2/v1se`
  （PAD 防伪，80×80 双模型）。编译精度 fp16、输出格式 SFB、compiler 3.39.0。
- `models.json` 的 `pipeline_id = yunet-mf-<precision>-npu:sha256(models.json)[:…]`——
  **gallery 目录名就是它的前 16 位**（结构级版本绑定，见 REFACTOR-CHECKLIST F04）。
- 每颗模型登记 `json/raw 的 sha256`、`hard_ops` 数、完整输入/输出 TensorType
  （shape/dtype/layout/merged_distrs）——运行前逐字节核对（boot_prepare/app checksum）。

## 三程序分工（进程=会话隔离）

| 程序 | 角色 | NPU 会话策略 |
|---|---|---|
| `wcis_camera_owner` | SDI 采集唯一持有者（DMA ping-pong，960×540@30） | 不跑 NPU；产出帧到 `/dev/shm/wcis-owner-<session>/` |
| `wcis_face_cached` | **NPU 单持有者**（session cache）：按需跑 yunet→mobileface→PAD | 一次 apply，session 缓存复用；模型就绪走 `models.json` 校验和 |
| `wcis_face_pl_retained` | PL DDR retained 上传路径（FAC1_customop_PL 预处理在 PL） | 与 cached 二选一（serve_photos `--pl-executable`/`--npu-cache-executable` 都给了，运行时按 preview 模式选择） |

- 预处理/后处理：`FAC1_customop_PL`（PL 侧 custom op）做图像预处理；host 侧只拿 JPEG/特征。
- `serve_photos.py` 是总入口（8091）：起 camera_owner 子进程、校验 `owner-current.json`
  的 boot_id/session、把帧"admission"给 NPU 模型——**单 owner 单 SDK 语义**（与 npuserv
  的设备独占结论一致，视觉侧早就这么设计了）。

## 与 npuserv / LLM 的关系

- **不共享会话**：视觉的 session 在它自己的 C++ 进程里；npuserv 的 LLM/vocoder 在各自
  worker 里。NPU 设备级互斥（2026-11-07 实验：第二会话破坏第一会话的 DMA 通道）意味着
  **视觉 NPU 推理与 LLM/vocoder 推理在时间轴上互斥**——实际表现为"逐请求交替"，因为
  双方都不长占（face 一次几十 ms，vocoder 62.9ms/句，LLM 每步 ~0.9s）。
- **CMA 是真正的共存开关**：LLM apply 会把 CmaFree 从 130MB 吃到 1.6MB（相机 DMA 失败、
  门禁 quarantine）——已两次实测。所以"Qwen3 常驻 + 相机活着"不成立；"vocoder 热切换 +
  相机"成立（apply 0.4s，前后 CMA 可恢复）。
- control 侧只见 HTTP：`control/adapters/video.py` 打 8091/8092/8093，board_verified
  开关在 control 的 status 里，翻真条件=REFACTOR-CHECKLIST F01（已完成）。

## 复现/验证入口

- 模型就绪：`curl :8091/api/health` → `model_cache_ready`/`hardware_admission_ready`。
- 幀级合同：`REFACTOR-CHECKLIST.md` F01 证据（workenv/video-f01/）。
- 身份库约束：F04 证据（8 项）。
