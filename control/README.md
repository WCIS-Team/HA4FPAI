# control —— 统一控制与业务资料层（unified-control 分支）

本包是 2026-11-05 联合规划（`../../wiki/mainchain/` 四篇 + 总规划）在 HA4FPAI 侧的
**第一版骨架**：SQLite 业务主库、统一 person_id、统一命令管线、/v1 服务、klar V2 适配、
旧声纹库只读预览。**视频域有意留空**：留空清单与接回方案见
[docs/VIDEO-DOMAIN-PLACEHOLDER.md](../docs/VIDEO-DOMAIN-PLACEHOLDER.md)。

分支基点：`origin/master` @ `c670285`（0.1.1 打包线，未夹带共享工作树 83 个未提交改动）。

## 跑法

```bash
# 自检（纯标准库，临时目录，不碰模型/网络服务/板端）
python -B -X utf8 control/tests/smoke.py

# 起服务（默认 127.0.0.1:18100，数据落 ./data/）
python -B -X utf8 -m control.service --db data/business.sqlite3 --token <token>
# 非回环监听必须带 --token（拒绝裸露启动）
```

## 模块地图

| 模块 | 职责 |
|---|---|
| `store.py` + `schema.py` | 业务主库：WAL/FK/busy_timeout、版本化迁移（checksum 防漂移）、store_id/generation/restore_epoch、domain_revision |
| `persons.py` | 人员与 external_identity_map：同名不合并、软删除撤销模板、合并保留旧 ID 映射 |
| `samples.py` | 登记模板契约：模型指纹注册、向量编码（显式 LE FP32/FP64）、维度/有限值校验 |
| `voiceprint_import.py` | 旧声纹库（boards 单向量 / lang 多样本）只读预览与同名冲突清单；**不写库** |
| `settings.py` | 设置 desired/applied：(scope, epoch, revision) 防乱序，迟到回执标 superseded |
| `commands.py` | 命令状态机：幂等键（同键异参 409）、危险动作一次性确认绑定、unknown 语义 |
| `events.py` | 事件 envelope：实例级 producer_id + seq（重启新实例，不撞号）、游标读取 |
| `outbox.py` | JSON 导出投影：不可变快照目录 + current 原子切换 + 消费游标防倒退 |
| `tasks.py` | 持久任务：(task_id, occurrence) 唯一；破坏性任务只生成待确认；worker 循环留待部署层 |
| `actions.py` | 动作注册表（单一来源）+ 直接短语表（整句匹配，否定/询问不误触）+ interpret |
| `adapters/klar.py` | klar V2 统一解析（执行器与语言路由共用）；safety_decision 不当系统鉴权 |
| `adapters/video.py` | **视频域留空桩**：全部返回 `video_domain_not_connected`，端点备忘在文件头 |
| `service.py` | /v1 HTTP 面：GET 只读、Bearer 会话、Origin 校验、8 MiB 上限、非回环必须带令牌 |

## /v1 端点（本分支骨架，未部署）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /healthz · /v1/capabilities · /v1/status | 公开只读 |
| POST | /v1/commands | 统一执行入口（幂等键、确认、目标、参数） |
| POST | /v1/interpret | 文本 → 提案（不执行；direct 整句 → klar V2 → LLM 交接） |
| POST | /v1/confirmations · /v1/session | 确认绑定；操作员会话 |
| GET/POST/PATCH/DELETE | /v1/persons… · /v1/persons/{id}/enrollments | 人员与登记（写走命令/资料层） |
| GET/PATCH | /v1/settings | 生效值含来源与 revision；recorder 域如实报未应用 |
| GET | /v1/events?after=cursor | 事件补读 |
| POST/GET/DELETE | /v1/tasks… · /v1/tasks/run-due | 任务管理与幂等触发 |
| POST | /v1/voiceprint/import-preview | 旧声纹库只读预览 |

## 既有入口的接入（后续工作包，本分支未动它们）

| 现有入口 | 现状 | 接法（规划依据） |
|---|---|---|
| `boards/console.py` GET `/api/run` | GET 会执行命令、无鉴权 | WP2：GET 改只读，动作走 POST /v1/commands；控制台作控制服务的客户端 |
| `klar/klar_executor.py` | 自带 /api/v2/parse 解析 + direct 子串匹配 | 换 `control/adapters/klar.py` 统一解析 + 整句匹配；动作落注册表 |
| `lang/server` KlarRouter | /parse 平铺协议，不调执行器 | 同上适配器；`router: klar` 与执行统一（WP9 六场景） |
| `web/ggradio` 配置写回 | 逐项覆盖 YAML、无事务 | 业务设置改 PATCH /v1/settings；YAML 只留启动/硬件默认 |
| `deploy/klar-executor.service` | `--serve` 参数源码已漂移 | WP1 核对：unit 与 argparse 对齐后再接入 |
| 两套声纹库 | 格式不兼容、同名覆盖 | `voiceprint_import` 预览 → WP6/7 显式迁移与切换（单一写入口=业务库） |

## 边界声明

- 本分支未部署、未迁移数据、未推送；板上现役服务不受影响。
- 共享工作树的 83 个未提交改动（十级链/声纹等）**不在本分支**，由其归属会话处理后另行合入。
- py3.8 兼容按构造保证（纯标准库、无 3.9+ 语法）；板端实跑按联合验收矩阵执行。
