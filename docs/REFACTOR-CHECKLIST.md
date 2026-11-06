# HA4FPAI重构总清单

更新：2026-11-06。**这是本次重构实施项的唯一勾选正本**。历史与实验详情见[起点和实施历史](REFACTOR-HISTORY.md)，模块/数据/进程边界见[目标架构](REFACTOR-ARCHITECTURE.md)。wiki阶段页保留历史，旧待办汇总改为入口；本页不代替项目总看板，也不按复选框数量生成项目完成百分比。

## 1. 范围、基线与勾选规矩

- 原始分叉：`c6702855eb229a6e5543db4812887c8628ccb739`；先在unified-control开发，后来才封存旧主线。
- 旧主线归档：`archive-2026-11-06`=`4efc50f9cdde2720c61d807502647eb514e9c312`。当前重构主线的已核提交=`130880477e06e31450050442296be7d605f58408`；共享HEAD仍909ba59，不随意挪动Qoder工作区。
- 视频参考唯一指定：`S:/work/WCIS/repos/fmql30tai-sdi-npu-face-recognition`，核对版本`f9e34bcd9f09ea451ce635a21dabb1198d5addbe`，重点`源码/ps_source/`；不搬整树，不把其他旧副本当现势正本。
- 本轮禁止修改、导入执行或间接测试`lang/npuserv`；该目录由Qoder维护。下面提到模型域接口时只定义合同，不实施该目录。
- 不以工期或难度删减正确性要求；可以调整计划，但必须记录变更理由、被替代ID及验收变化，不把旧未做项悄悄删除。
- `[x]`只表示**标题描述的那一步**已做到，不表示整个领域上线；`[ ]`仍待实施/验收。模块已有实现和生产真实消费方接通必须拆成不同项。
- 做完一步即时勾选，补：完成日期、精确源码SHA、证据文件/测试名称、实际读数、边界。无证据不勾；仅规划、写了代码、接口返回200不能代替完整验收。
- 出现回归时恢复`[ ]`并记录原因；已有模块若被目标架构替代，保留历史勾选并链接替代待办。
- 新增脚本/日志/安装包/临时库进workenv；正式工具随所属代码包登记。wiki本地更新不推GitHub；本轮文档不提交、不推、不派发Actions。

## 2. 已完成步骤：按组件与功能核对

下面完成的是已存在的模块或明确实验，不是对生产状态的总体担保。测试来源与局限在历史文档第3节。

- [x] **DONE-01｜保存重构前主线并完成主干合流。**
  - 组件/方法：archive分支固定4efc50f；aa141c9/909ba59合入旧主线，master快进，已合临时分支名删除。
  - 证据：本地Git对象与祖先链。边界：归档分支不是生产DB备份；三级gitlink仍有A01错误。
- [x] **DONE-02｜建立业务SQLite连接与短事务骨架。**
  - 组件：`control/store.py`；WAL/FULL/FK/5000ms、RLock、BEGIN IMMEDIATE、异常回滚。
  - 证据：d43fe50及文件库PRAGMA现场探测。边界：每进程一把锁不等于单写进程，按A02改。
- [x] **DONE-03｜版本化DDL与库世代标识。**
  - 组件：`schema.py`/`store.py`；schema_migrations checksum、store_id、generation、restore_epoch、domain_revision。
  - 证据：d43fe50模块测试。边界：CHECK覆盖、备份恢复与多进程世代切换待B组。
- [x] **DONE-04｜稳定人员ID和外部旧身份映射。**
  - 组件：`persons.py`；person_id独立于display_name，external_identity_map保存来源及legacy key。
  - 证据：同名不同ID、重复来源映射测试。边界：真实face/voice消费方未统一切库，按E03/G04验收。
- [x] **DONE-05｜模型登记与显式向量样本接口。**
  - 组件：`samples.py`；face_samples/voice_samples、模型登记前置、显式float32-le/float64-le、dim与有限数值校验、active/revocation。
  - 证据：样本模块测试。边界：并未完成真实图片/音频提取闭环或生产指纹批准。
- [x] **DONE-06｜设置desired修订与回执栅栏骨架。**
  - 组件：`settings.py`；5个注册键、expected_revision冲突、scope/epoch/revision回执准入。
  - 证据：更新与迟到回执测试。边界：hot入库即applied语义不够、applier缺失，D组待补。
- [x] **DONE-07｜统一动作注册、命令与幂等。**
  - 组件：`actions.py`/`commands.py`；提案→命令、规范参数、相同幂等键不同参数409、执行结果状态。
  - 证据：d43fe50及smoke。边界：外部动作幂等、unknown对账和重启恢复仍需端到端故障验收。
- [x] **DONE-08｜危险动作一次性确认骨架。**
  - 组件：commands确认记录与principal/session；绑定动作、参数摘要、主体与到期时间。
  - 证据：确认过期/复用等模块检查。边界：录像删除、retention等真实域写入口未全部接入。
- [x] **DONE-09｜事件与投影模块。**
  - 组件：`events.py`/`outbox.py`；producer_id+seq、不可变导出目录、current切换、consumer_offsets防倒退。
  - 证据：事件/导出测试。边界：真实消费方落地、断线补投与持久回执发件箱仍待接。
- [x] **DONE-10｜业务HTTP服务骨架。**
  - 组件：`service.py`；/v1资源、Bearer、Origin检查、健康与能力接口。
  - 证据：真实本机HTTP测试含无令牌/跨源拒绝。边界：资源上限、主体权限、生产暴露方式仍待H03。
- [x] **DONE-11｜executor与console的统一控制开关。**
  - 组件：`control_client.py`、`klar_executor.py`、`boards/console.py`；HA4FPAI_CONTROL_URL启用新路径，失败不静默回退，GET执行入口405、POST副作用。
  - 证据：838273d/2fe8675。边界：开关默认关；ASR→router→执行器真实调用图未全链验收。
- [x] **DONE-12｜语音桥与整句匹配。**
  - 组件：`voice_bridge.py`和klar adapter；文本→提案→命令→可念结果；否定/疑问不误触，LLM handoff不伪造执行。
  - 证据：桥HTTP测试。边界：真实音频/TTS和确认多轮仍待G组。
- [x] **DONE-13｜视频V1–V5 HTTP适配器。**
  - 组件：`adapters/video.py`；识别、受控快照、画面就绪地址、录像模式回读、列表/回放URL，区分不可达/无帧/服务错误。
  - 证据：5cdc45d、模拟合同37检查。边界：所有权未搬动；board_verified仍应false，真实服务合同按F01核验。
- [x] **DONE-14｜两格式旧声纹只读预览。**
  - 组件：`voiceprint_import.py`；单向量boards格式/多样本lang格式识别，人数/坏向量/缺指纹/跨源同名报告。
  - 证据：smoke/test_migrate。边界：当前Python解析，不是JSON1 SQL迁移桥，gallery schema尚未覆盖。
- [x] **DONE-15｜声纹隔离导入与实体种子工具。**
  - 组件：`migrate.py`；identity幂等、同名显式决策、activate=False；klar实体/区域种子工具。
  - 证据：b8ed0bb、39检查。边界：真实实体消费者与生产导入仍待C05/C06。
- [x] **DONE-16｜任务occurrence与epoch退出检查。**
  - 组件：`tasks.py`/`worker.py`；到期任务、唯一occurrence、世代变化退出。
  - 证据：模块任务检查。边界：当前独立进程直写库不满足目标所有权，A02必须替换部署方式。
- [x] **DONE-17｜受控媒体上传/读取骨架。**
  - 组件：`media.py`；4类MIME白名单、32MiB、原子文件写、哈希、realpath/commonpath路径守卫、删除态拒读。
  - 证据：3f3d231与smoke。边界：还缺真实文件格式/解码检查、Range与孤儿协调。
- [x] **DONE-18｜recorder回执接收端。**
  - 组件：`receipts.py`与POST /v1/recordings/receipts；operation_id去重、不复活purged、abandon只记操作。
  - 证据：3f3d231模块检查。边界：发送端持久outbox和真实文件对账未接。
- [x] **DONE-19｜control deb组装与结构自检。**
  - 组件：`packaging/build_control_deb.py`、postinst与包测试；纯Python ar/tar、MD5清单、SHA归档、拒覆盖、令牌引导。
  - 证据：3bfea68、12检查。边界：不是安装生命周期验收，当前安装路径与unit不符，A03/H04未做。
- [x] **DONE-20｜手动CI登记control四套。**
  - 组件：`ci/config.json`与既有workflow_dispatch；语法扫描覆盖control、quick/full注册。
  - 证据：c3a8538云端py3.8/py3.13/Windows三个矩阵成功。边界：之后SHA未因这一次run自动获证。
- [x] **DONE-21｜executor失败语义端到端测试。**
  - 组件：`test_executor_bridge.py`；真实HTTP、dry_run旁路、否定不执行、设备不可达、401、控制服务不可达不走旧通道。
  - 证据：1308804，7检查，已注册CI。边界：不是从真实ASR到TTS的全链。
- [x] **DONE-22｜板端测试隔离缺陷修复。**
  - 组件：smoke/executor测试注入关闭端口；从依赖默认809x改为显式失败条件。
  - 证据：初测84/88及6/7，修后88/88及7/7（会话记录）。边界：首次真实请求的副作用按A04核对。
- [x] **DONE-23｜目标解释器模块实验。**
  - 组件：板端Python3.8.10、临时control/klar副本、五套测试及compileall。
  - 证据：会话记录183检查通过。边界：完整日志与退出码需H06补存证，不能当生产部署/长稳通过。
- [x] **DONE-24｜真实旧声纹临时库预演。**
  - 组件：workenv/migrate-real-preview/run.py；2人2向量、192维，首次2/2，重跑跳过2，active0。
  - 证据：旧库预览与临时库计数。边界：未知模型演练标签不得生产激活；原业务库未切换。
- [x] **DONE-25｜SQLite板端能力只读探测。**
  - 组件：Python sqlite3实际链接3.31.1；json_extract/json_valid成功、jsonb不可用、PRAGMA文件库回读。
  - 证据：workenv/migrate-real-preview/sqlite_probe.py。边界：需在实际生产连接做A05，不依据发行版猜3.40+。
- [x] **DONE-26｜指定视频源码只读架构核对。**
  - 组件：persistent_gallery、serve_photos、recording_service/library、face_history/store与owner文件；参考版本见第1节。
  - 证据：历史库已有WAL/FULL和BLOB CHECK；录像已有恢复/读者保护；旧enroll直接写gallery。
  - 边界：这里勾选的是源码调查，不是它们已与控制库集成；V7/V8改为复用与接入任务。

## 3. A组：纠错与部署前置

- [x] **A01｜修复三级仓库指针链。组件：Git、.gitmodules、gitlink。**（2026-11-06 完成）
  - 实施：`WCIS-FPAI-repos` 提交 `9240706a`（HA4FPAI gitlink → `1d2ddb7`，staged 仅此一条，未夹带 ha-small/ 等他人条目）；主库提交 `832f1c4` 把 `repos` gitlink 从误指的 HA4FPAI 提交（1308804，对象不在中间仓）改回中间仓提交 `9240706a`（index-info 安全写法，diff 仅一行）。
  - 证据：隔离 clone `workenv/tmp/a01-clone-verify` 逐级 `submodule update` 成功——主库 repos 指针 `9240706a` → repos 检出 → HA4FPAI gitlink `1d2ddb7` → 检出 HEAD 即 `1d2ddb7`；三级远端均可取。
  - 边界：wiki gitlink 独立维护不在本验收内；此前空树事故的止损提交（0ea884d/a9feba8）不能替代本项。
- [x] **A02｜建立唯一写入协调器与同进程调度。组件：sqlite3、Scheduler、control.service。**（2026-11-06 完成）
  - 实施：新增 `control/scheduler.py`（线程内周期 run_due；epoch 变化置 exit_requested，服务整体退出码 3 交 systemd 重启）；`App(scheduler_interval=)` 启动即建并 start 调度线程，与 HTTP 请求共享同一 App/写路径；`/healthz`、`/v1/status` 暴露调度状态；`ha4fpai-task-worker.service` 降级 `Type=oneshot` 手工工具并标 DEPRECATED，不再常驻直写库。
  - 证据：`control/tests/test_a_group.py` 12/12（run_once 执行到期任务、任务命令经同一 App 落库、occurrence 幂等不重跑、epoch 栅栏请求退出、/healthz 调度线程 running、单元 oneshot 化）；CI quick 10 套 PASSED（control-a-group 注册进 quick/full）。
  - 边界：单进程内多线程仍经 Store 的 RLock＋BEGIN IMMEDIATE 串行（规划允许的形态）；跨进程单写者按部署约定执行（只跑 control 一个常驻进程）。
- [x] **A03｜修正control deb与systemd路径。组件：unit、postinst、control.in。**（2026-11-06 完成）
  - 实施：control 单元改 `WorkingDirectory=/usr/lib/ha4fpai-control`＋`--token-file /etc/ha4fpai-control/token`＋`--scheduler-interval 60`；postinst 建 `/etc/ha4fpai-control`（0700/0600 令牌）并在升级时自动 disable 误启的旧 worker 单元；control.in 包描述同步新布局。
  - 证据：`test_a_group.py` 三项布局断言。deb 的干净环境安装/升级/卸载仍归 H04（依赖 H03），本项只闭环"包路径与单元一致"。
- [ ] **A04｜核对初次板测副作用并永久隔离测试。组件：809x状态、测试fixtures。**
  - 方法：读回录像模式/媒体清单与相关日志，记录未知影响；不擅删用户文件。失败mock服务持有端口或隔离命名空间，避免"选完再关闭"竞争。
  - 验收：测试不能触达生产可变API；现场模式有明确来源，必要恢复按用户期望执行并记录。
- [x] **A05｜连接配置与存储启动门禁。组件：SQLite PRAGMA、verify_pragmas。**（2026-11-06 完成）
  - 实施：`Store.verify_pragmas(file_db=True)` 读回 foreign_keys/busy_timeout/journal_mode/synchronous，文件库必须 `wal/2/5000/1`，不符即 RuntimeError 拒绝启动；`Store.__init__` 每连接自检；`:memory:` 豁免并显式标记非生产形态。
  - 证据：`test_a_group.py` 两项（读回 wal/2/5000/1；synchronous 降级被拒）。挂载 UUID/ext4 guard 归 H02，不在本项。

## 4. B组：数据库不变量与持久化

- [x] **B01｜追加约束迁移与旧库升级报告。组件：schema_migrations、SQLite3.31。**（2026-11-06 完成）
  - 实施：`MIGRATION_2`（追加版，不改 v1 checksum）；3.31 兼容的"建新表（带CHECK）→校验复制→换表→重建索引"改表法，`PRAGMA legacy_alter_table=ON` 保持其余表 FK 引用名不变；**顺序＝先子表后父表**（旧子表先换、父表才无引用者）。坏行进 `persons_rejects`（`rejected_at DEFAULT datetime('now')`）可审计，不静默丢。
  - 证据：`control/tests/test_schema_constraints.py` 23/23——干净 v1 升级行数不丢（2/1/1/1）、schema_version=2、坏 status 行/dim0 模型入 rejects 且未迁入、合法数据零隔离。
  - 边界：v2 样本表**故意不带 REFERENCES**（跨表一致性由 `samples.enroll` 同事务校验，有记录的取舍）；升级前快照/中断续跑归 H08 演练，本项只闭环"旧库升级不丢数据+坏行可审计"。
- [x] **B02｜人员/模型/命令通用结构约束。组件：CHECK/UNIQUE/NOT NULL。**（2026-11-06 完成）
  - 实施：persons（id≥8、姓名 1–40 非空白、status 枚举、revision≥1）；models（modality/dtype/metric 枚举、dim>0、sha256 64 位可选）；commands（id≥8、action≥1、status 六态枚举、digest 64、result_json 可空 JSON、revision≥0）。
  - 证据：直 SQL 非法 status/负 revision/非法 modality 均 `IntegrityError`；业务错误仍由 Python 入口给出稳定 code。
- [x] **B03｜样本编码和模型一致性。组件：BLOB CHECK、dim×dtype 字节宽。**（2026-11-06 完成）
  - 实施：voice/face_samples 单表 CHECK：`typeof(blob)='blob'`、`length(blob)=dim×{4|8 按 dtype}`、blob_sha256 64 位、active∈{0,1}、dtype 枚举。dim 与模型的跨表一致性由 `samples.enroll` 同事务校验（dim_mismatch 覆盖）。
  - 证据：直 SQL 截断 BLOB（192维/40字节）被拒、非法 dtype 被拒、非 BLOB 被 typeof 拒、active 越界 UPDATE 被拒；入口 dim_mismatch 断言。期间修复了 smoke 的假 digest 种子（`digest-x`→真实 SHA）。
- [x] **B04｜JSON字段与数值范围校验。组件：JSON1、settings definitions。**（2026-11-06 完成）
  - 实施：commands 的 params_json/result_json 加 `json_valid`；settings 的 value 加 `json_valid`、value_type 枚举；`Settings._validate` 补 NaN/Inf 拒绝与 `min/max`（volume 0–100、阈值 0–1）。
  - 证据：直 SQL 非 JSON params/非法 status/非 JSON settings value 均被库拒；入口三条范围拒绝；合法值入库且 `json_valid=1`。
- [x] **B05｜在线备份与恢复世代。组件：sqlite3.Connection.backup、manifest、epoch。**（2026-11-06 完成）
  - 实施：新增 `control/backup.py`——`backup()` 用 `Connection.backup()` 生成**在线一致性快照**（不拷主库），落 `manifest.json`（db sha256/字节、schema_version、restore_epoch、domain_revision、store_id、sqlite_version、媒体清单含路径/字节/sha256）；`verify()` 校验 sha256＋`integrity_check`＋`foreign_key_check`，不符抛 `BackupInvalid` 拒绝；`restore()` 校验后原子替换、清旧 `-wal/-shm`、打开新库**提升 restore_epoch**（A02 世代栅栏，使旧任务/回执/投影失效）。内存库明确拒绝备份。
  - 证据：`control/tests/test_backup_ops.py`（备份可开 integrity ok/fk 空、媒体清单含 sha、恢复回滚数据并把 epoch 1→2、篡改 sha 与坏库 integrity 均拒绝、内存库拒绝）。
- [x] **B06｜WAL和磁盘故障运维。组件：wal_checkpoint、空间/队列指标。**（2026-11-06 完成）
  - 实施：新增 `control/ops.py`——`checkpoint(mode)`（PASSIVE/FULL/RESTART/TRUNCATE，返回 busy/log/checkpointed/wal_bytes；常规 PASSIVE、维护窗口 TRUNCATE）；`disk_stat()`；`healthy()` 按 `min_free_bytes`(默认 128MiB)/`wal_warn_bytes`(64MiB) 阈值给 warnings；`map_sqlite_error()` 把 BUSY/LOCKED/READONLY/IOERR/FULL 映射成 `db_busy/db_locked/db_readonly/db_io/db_full`（3.11+ 用 sqlite_errorcode，老版本按消息兜底）。
  - 证据：`test_backup_ops.py`（PASSIVE/TRUNCATE 执行、非法 mode 拒绝、磁盘读数、四种错误映射、**长读者时 PASSIVE 不能全量 checkpoint（checkpointed<log_pages 可观测）、释放后恢复全量**、阈值越界→warnings、正常→ok）。CI quick 12 套 PASSED。

## 5. C组：JSON1迁移桥与正式切换

- [x] **C01｜JSON1能力与版本合同。组件：json_valid/json_type/json_extract/json_each。**（2026-11-07 完成）
  - 实施：新增 `control/json1.py`——`probe()` 运行四函数的行为探测（含 `json_valid('{bad')==0` 语义校验）＋版本比对 `MIN_TESTED=3.31.1`；`require()` 不达标抛 `Json1Unavailable`（含版本与缺失项），不降级到 Python 语义；`has_jsonb` 仅作信息项不作要求。
  - 证据：`test_json1_staging.py`（本机探测通过、版本达标、伪造缺函数连接被如实报出且 require 拒绝）。板端 3.31.1 已由套件同一探测覆盖（SQLite 3.31.1 支持四函数）。
- [x] **C02｜gallery及两类声纹JSON暂存桥。组件：SQL暂存表、source manifest。**（2026-11-07 完成）
  - 实施：`MIGRATION_3` 加 `migration_source/stg_person/stg_sample/migration_reject`；`control/staging.py:load_source()` 按内容识别 gallery-schema2 / voice_boards / voice_lang，按源展开成暂存行，**坏行（坏 JSON/缺名/null 向量/非数值/维度不符/非列表）逐条入 `migration_reject` 并保源定位（src_key）**；原件 sha256/字节/schema 入 manifest；同源重载先清旧行（幂等）。
  - 证据：`test_json1_staging.py` 三源逐行计数——gallery 6=4ok+2bad、boards 4=3ok+1bad、lang 5=4ok+1bad（**成功+坏=源总数**）；SQL `json_extract` 可从 `vector_json` 取字段。
- [x] **C03｜暂存到业务表的幂等导入。组件：迁移用例、identity_map、事件。**（2026-11-07 完成）
  - 实施：`staging.import_staged()`——从暂存结构经 SQL 取字段，人员走 `persons.create`/`identity_add`（namespace=`stg:<source_id>`，legacy key 带源命名空间，不偷换全局 ID），样本走 `samples.enroll`（跟随模型编码）；同名跨源需显式 decision（不自动合并），`activate` 控制隔离；`stg_*` 行 `imported_*` 标记 + identity_map 双保险做幂等。
  - 证据：首次建 2 人/入 2 样本；**重跑 created=0/enrolled=0 人数不变**；源记录经 identity_map 反查 person_id；同名跨源 → `same_name_needs_decision`；gallery→face 同样建 2 人/idempotent。
- [x] **C04｜真实模型审计与激活批准。组件：模型SHA、pipeline、预处理manifest。**（2026-11-07 完成）
  - 实施：`staging.audit_model()`——核对登记模型 modality/dim/dtype/metric 与期望、`model_sha256` 是否在档、可选 `manifest_path` 的实际 sha，输出样本 dim/dtype 分布作证据；`rehearsal=True` 时带 `legacy_pipeline_id` 或 `campplus-real` 的模型判为**不能当生产身份**。
  - 证据：`test_json1_staging.py`（审计通过给样本证据；dim 期望不符失败；演练模型当生产被拒）。未做的：真实 gallery/声纹文件的生产审计与激活属 C05。
- [ ] **C05｜生产资料清点、停写与导入。组件：旧图库/声纹/recorder侧车。**
  - 方法：现场hash快照、维护窗口停旧写、dry-run、隔离apply、人数/向量/文件对账，再按批准激活。
  - 验收：真实生产库唯一权威，原件可回取；失败可恢复且不双写；空speakers目录不冒充完整迁移。依赖B05/C04/G04。
- [ ] **C06｜实体种子与版本投影真实消费。组件：klar registry、outbox/current/consumer offsets。**
  - 方法：种子幂等入库、业务修改后生成不可变快照与SHA；消费端确认版本并失效旧缓存。
  - 验收：新增/撤销实体可观察、旧投影拒绝覆盖、断线重启能补版本。依赖A02/B05。

## 6. D组：设置applier和ggradio

- [x] **D01｜全参数注册与分类。组件：DEFINITIONS、ggradio parse_file。**（2026-11-07 完成）
  - 实施：`settings.DEFINITIONS` 每键加 `kind`(business)/`consumer`(audio·face·voiceprint·recorder·ui)/`sensitive`/`overlay`；`definitions()` 出注册表视图、`sensitive_keys()`；`OVERLAY_KEYS` 白名单（只 business+overlay，不含 ui.theme/凭据）。覆盖文件只能写白名单键，不接任意文件路径/shell。
  - 证据：`test_settings_applier.py`（5 键均带分类、白名单排除 ui.theme）。
- [x] **D02｜消费确认才标applied。组件：Settings.effective/apply_feedback。**（2026-11-07 完成）
  - 实施：`effective()` 对**所有 mode** 只有"`settings_applied` 版本==desired 且 epoch==当前"才算 applied，新增 `pending`；`apply_feedback(epoch,revision,keys,digest)` 绑定 epoch/版本/**值摘要**，拒 `stale_epoch`/`future_revision`/`digest_mismatch`/旧版本标 superseded。
  - 证据：矩阵通过——入库未消费 applied=False/pending=True；正确回执转 applied；错 epoch、未来修订、摘要不符均拒；旧回执不覆盖新值。
- [x] **D03｜配置生成与原子applier。组件：YAML默认、生成覆盖、fsync/replace。**（2026-11-07 完成）
  - 实施：新增 `control/applier.py`——`render_overlay` 只取白名单业务键→渲染 YAML→**自检(key/值一致)→临时文件 fsync→备份旧 .bak→原子 os.replace**；dry_run 不落盘；失败保旧文件；业务源仍是 DB。
  - 证据：`test_settings_applier.py`（dry_run 不落盘、写后回读一致、不含非白名单键、二次写留 .bak）。
- [x] **D04｜热更新与受限重启合同。组件：lang消费API、systemd白名单动作。**（2026-11-07 完成，真实 systemd 执行属部署/板端）
  - 实施：`applier.restricted_restart(names, runner, health_probe)`——**白名单**仅 `lang-server-http/loop/voice`，`npuserv/ha4fpai-task-worker/ha4fpai-control` 与任意未知/注入名一律 `refused`；可注入 runner/健康探针；健康失败记 `failed` 不谎报。
  - 证据：白名单 dry_run 计划、拒绝 npuserv/"rm -rf /"、注入 runner 成功与探针失败两路。边界：真实 `systemctl restart` 与在途请求排空在部署机执行，代码合同已定。
- [x] **D05｜ggradio三类写入口统一API。组件：write_param/组合设置/reset_defaults。**（2026-11-07 完成）
  - 实施：`Settings.write_param`（单改带 expected_revision）/`write_batch`（批量原子，任一不符整批不落库）/`reset_defaults`（回默认）；均走同一 `update` 事务。
  - 证据：单改、批量冲突整批拒绝且不改任何键、批量合法一次提交、reset 回默认。
- [x] **D06｜recorder模式版本回读。组件：8093/api/mode、apply_feedback。**（2026-11-07 完成，画面/8093 实机接线属 F02）
  - 实施：`Settings.recorder_feedback(mode, revision, epoch, operation_id, armed)`——校验 revision==当前 desired（否则 `stale_revision`）、实际 mode==desired（否则 `mode_mismatch`）、经 apply_feedback 落 applied；`armed` 与 `actively_recording` 独立表示。
  - 证据：旧 revision 拒、模式不符拒、匹配接受且 armed→actively_recording、armed 独立。边界：真实 8093 `/api/mode` 与 operation 回执在 F02 接线。

## 7. E组：登记、识别与融合授权

- [ ] **E01｜声纹登记真实执行端改道。组件：ggradio录音/上传、现有spk、samples用例。**
  - 方法：提向量与资料提交分离，携模型指纹到指定person_id；追加/替换/撤销写业务库；模型按需加载，不引入npuserv执行。
  - 验收：真实音频闭环，重复/错误/撤销可追溯，旧JSON只读。依赖C04/D05。
- [ ] **E02｜无旧图库副作用的人脸提取接口。组件：视频serve_photos/infer、samples.enroll。**
  - 方法：现有/api/enroll会gallery.add，必须拆extract-only或等效无写提取合同；返回embedding、pipeline、质量与源哈希，由业务库提交person_id。
  - 验收：提取不另建JSON身份；多脸/低质/错模型拒绝；真实登记一人多样本可用。依赖C04/F01。
- [ ] **E03｜识别消费者统一模板来源。组件：PersistentGallery缓存、声纹比对。**
  - 方法：消费业务API/有版本只读投影；保留pipeline与缓存优化；撤销/世代更改失效；不维持第二独立登记库。
  - 验收：新增可识别、撤销即不授权、断库策略明确且不越权。依赖E01/E02/C06。
- [ ] **E04｜融合授权按同一身份与新鲜证据。组件：verification_session、frame lease、challenge。**
  - 方法：绑定挑战/有效期/boot与帧新鲜度；face/voice必须同person_id，拒重放/旧缓存；授权动作走统一确认/权限。
  - 验收：A脸+B声不能合成授权；掉帧/旧boot/过期/撤销故障矩阵。依赖E03/G02。

## 8. F组：指定视频域复用与集成

- [ ] **F01｜V1–V5真实合同核验。组件：8091/8092/8093与video adapter。**
  - 方法：将指定源码合同与板端版本/hash对齐；识别嵌套字段/503、快照编码、画面session、模式、分页/回放逐项验收；区别transport 200与业务成功。
  - 验收：真实帧、源哈希、错误码与新鲜度证据；能力分别验收后才翻board_verified。依赖A04/A05。
- [ ] **F02｜recorder持久回执发送与对账。组件：域SQLite outbox或既有持久日志、控制inbox。**
  - 方法：复用段封存点和trash/purge日志，operation_id有内容摘要，事务/文件提交后入outbox；至少一次投递、退避、死信、启动扫描对账。
  - 验收：断网/断电/重复/乱序不丢目录状态，purged不复活；文件齐备才complete。依赖A02/B05。
- [ ] **F03｜复用录像生命周期并接统一动作。组件：recording_library的delete/restore/purge/readers。**
  - 方法：保留读者保护、purging恢复、配额、retention/compression；统一权限/确认/操作ID；跨文件与业务镜像不伪装ACID。
  - 验收：播放时清理被明确拒绝或等待，重启续清，retention危险确认生效，产物一致。依赖F02。
- [ ] **F04｜复用已有face_history SQLite而非重建。组件：HistoryStore/extractor/quality。**
  - 方法：沿用512维FP32归一化、容量与pipeline/唯一帧约束；补统一event/frame关联和对外查询合同；确认现场部署版本及备份/留存。
  - 验收：重复帧不同内容报错，检索与容量边界、旧pipeline拒绝，历史不变登记身份。依赖F01/C04。
- [ ] **F05｜媒体真实性与文件/数据库协调。组件：media_assets、解码/魔数、恢复扫描。**
  - 方法：在MIME/32MiB基础上验证真实编码、尺寸/解码预算与path；为文件先落盘DB后提交增加孤儿清单与恢复协议；不随意删除未知文件。
  - 验收：伪PNG/畸形MP4/WAV、压缩炸弹/超限、半写、掉盘与符号链接拒绝；媒体引用可对账。依赖A05/B05。
- [ ] **F06｜播放与网络鉴权接入。组件：既有Range实现、同源网关、客户端session。**
  - 方法：复用8093的206/416及读者租约；核实后缀/空文件/中断范围合同；鉴权/流式代理/板外可达URL；原始媒体不全量缓冲到业务进程。
  - 验收：板外浏览器拖动/断线/换session有效，未授权拒绝，不泄漏回环URL或路径。依赖H03/F03。

## 9. G组：真实语音、UI与旧路径退役

- [ ] **G01｜核实并闭合ASR/router到执行器。组件：lang core/router、klar、ControlClient。**
  - 方法：画真实调用图并用相关ID追踪；若路由未调用executor则补唯一执行接口，避免桥和klar重复解释/执行；保留闲聊handoff。
  - 验收：真实音频或录音fixture→识别→一次命令→回执→TTS，不能只以executor单测代替。
- [ ] **G02｜语音失败与危险确认多轮。组件：session、pending_confirmation、TTS结果。**
  - 方法：固定回复/否定/疑问/澄清/确认/超时unknown分别处理；回复只从结构化结果生成，LLM话术不作设备证据。
  - 验收：六类场景、重复输入/音频重试/断网均不双执行或虚报；敏感动作主体正确。依赖G01/A02。
- [ ] **G03｜业务控制台/视频工作台读模型。组件：现有网页与iframe、API/event游标。**
  - 方法：接人员/样本/录像/任务/命令历史及desired/applied；需要通知时用可续游标，不以推送消息代权威状态。
  - 验收：每个按钮可追command/event，pending/error和未验能力清楚；刷新/重连不会重复副作用。依赖D05/E03/F03。
- [ ] **G04｜停旧写入口与正式切换WP7。组件：旧gallery/声纹JSON、YAML写、设备控制旁路。**
  - 方法：列全入口逐个封写/代理统一API；旧格式只读导出；功能开关有版本与回退条件，不能长期双写不同权威。
  - 验收：抓写审计证明每类资源仅指定权威可写；回滚不重新激活第二写库。依赖C05/E03/D05/G01。

## 10. H组：交付、运维与最终验收

- [ ] **H01｜控制服务生产安装与开机恢复。组件：systemd、服务用户、环境文件。**
  - 方法：按A02/A03布局安装，建立健康/readiness/schema/epoch输出，限定内存与请求/线程/队列上限；启用动作与版本留回执。
  - 验收：真实启动/开机/异常重启可恢复，不是/tmp测试通过就勾。依赖A组。
- [ ] **H02｜数据盘持久化与掉盘策略。组件：ext4/UUID guard、DB/WAL/SHM。**
  - 方法：同可靠文件系统，核对fsync语义；挂载丢失拒绝写，不另建空库；记录只读/维护模式。
  - 验收：掉盘/恢复/异常关机后库与媒体一致，状态页区分不可用与空数据。依赖A05/B05。
- [ ] **H03｜鉴权、凭据与网络边界。组件：Bearer/主体权限、服务间身份、浏览器网关。**
  - 方法：令牌原子0600/轮换/目录权限；回环或受控反代；Origin不是权限；视频域原始写API也要防旁路；错误脱敏。
  - 验收：无令牌/错角色/跨源/过期确认/伪回执/路径攻击矩阵；业务日志不含凭据。依赖A02。
- [ ] **H04｜control deb安装生命周期。组件：postinst/prerm/postrm、校验和、systemd verify。**
  - 方法：干净Ubuntu20.04容器安装启动、升级、卸载保数据、重装；维护脚本幂等与目录权限；逐文件验证hash而非只数行。
  - 验收：从包独立复现运行、旧数据保留，失败可恢复；架构/版本字段与内容相符。依赖A03/H03。
- [ ] **H05｜lang发布内容及Qoder版本交接。组件：包清单/lock/服务配置。**（2026-11-06 npuserv 段闭环＋ERNIE/Qwen3 真件首次点火，剩余质量与打包待做）
  - ✅ npuserv 段：qoder 完成 registry 适配 llm/models 布局（两条真件 adapter 路径修正）＋compiled_with 声明字段＋/models 暴露＋布局契约守门 2 项（lifecycle 37→40），77 项测试（40+12+22+3）本机全绿；lead 完成板端落地——两条软链（/root/llm/models/）、板上 models.json/server.py 同步仓版、GET /models 三条 adapter_exists 全真＋artifacts_present 全真、mock 生命周期 load→generate→unload 板端实测通过（提交 608f886；指针链 repos 9a0a1ec3 → 主库 f83b342）。
  - ✅ **ERNIE-4.5-0.3B 真实件首次点火（2026-11-06 19:50–19:52，板 113）**：`/load` apply 40.8s、loading 104.8s → `ready`，设备初始化成功（icore FMSHZGV3TECH-AID / device zg330aiu，dma 0x80000000 / npu 0x40000000）；`/generate` text="你好" max_new=8 → ids `[93956,94042,3507,34377,10359,94105,23,23,94042]`、text `"，你今天过得怎么样？\n\n你"`（4.6s，10 events，真实连贯中文）；`/unload` rc=0。证据：`workenv/ha-npu-ignite-2026-11-06/ernie_ignite.log`＋`worker_5efec6ac.log`。**并更正一处旧判断**：加载前后 CmaFree 都只有 9–14MB、几乎不变，说明模型从驱动自有 NPU 池（0x40000000）分配，低 CmaFree 是正常的（udmabuf0 开机占 128MiB＋驱动预留），**不是泄漏**，无需 reboot。
  - ✅ **Qwen3-0.6B 真实件首次点火（2026-11-06 19:53–19:56，板 113）**：`/load` apply 171.5s、loading 172.0s → `ready`（kv dtype `@fp(16)` shape `[1,8,512,128]`，设备初始化成功）；`/generate` ids=[9707,1879] max_new=8 decode:false → ids `[508,508,508,508,508,508,508,508]`、gen 8、13.7s、10 events、last_slot 9、pos_rule=vendor。**结论边界：这是该件在板上第一次真的跑出 token（此前只到 apply、零 token，#212），但输出退化（8 个全是同一 token 508），且走的是 ids 路径、未验 tokenizer/文本——只证明"能加载、能出 token"，未达可用质量**（退化定位见下条）。证据：`workenv/ha-npu-ignite-2026-11-06/qwen3_ignite.log`＋`worker_3682c7c9.log`。
  - ✅ **ERNIE 多 prompt 稳定性（2026-11-06 23:16–23:18，板 113，单次加载 apply 41.5s）**：5 组 prompt 全部跑通——「你好」→连贯闲聊；「三句话说明边缘计算」→结构完整解释（64 token/32.5s）；「翻译：今天会议推迟到下午三点」→**英文正确**「The meeting was postponed to three o'clock in the afternoon.」并正常 EOS；同一 prompt 重跑**逐 token 相同**（确定性/贪心可复现）。质量属 0.3B 正常（偶带 `00:00:00.000`、`[你的名字]` 类模板痕迹），非退化。证据：`workenv/ha-npu-ignite-2026-11-06/ernie_multi.log`。
  - ⚠️ **Qwen3 退化已定位（2026-11-06 23:09–23:14，板 113）**：`pos_rule=vendor` 与 `uniform` **两臂输出完全相同**（text "你好" 16 token 均 `[198×16]`＝换行 `"\n"`，30s；tokenizer 正常，prompt=9）。且首 token 随 prompt 变（ids 路径 508 / text 路径 198）、其后恒定重复 ⇒ **不是 pos_rule，而是 decode 步 KV 回喂缺陷：模型看不到已生成 token**。对照证据：adapter vendor 臂整块替换 KV 与 `q3_infer_board.py:run_decode` 返回 `arrays[1:]` 整块替换一致、uniform 臂走切片写回，两种结构不同却同错 ⇒ 问题在 KV feed/布局（`past_key_values.i.key/value` 或 out 索引），非更新策略。**属 Qwen 模型驱动侧，未擅自改**；下一步需板端 dump KV 校验和＋对照 vendored `run_prompt/run_decode` 做单元级定位。证据：`workenv/ha-npu-ignite-2026-11-06/q3_ab.log`。
  - 待做：Qwen3 KV 回喂缺陷的驱动级修复与复测（owner：Qwen 模型侧）；ERNIE 多轮上下文（当前 npuserv generate 无状态，多轮需上层拼 prompt）；compiled_with 与板端 runtime 比对仍是无代码声明；lang deb 包清单与其余组件打包核对。
- [ ] **H06｜no-npu CI、证据留存与故障注入。组件：ci profiles、JUnit/原始日志、SHA manifest。**
  - 方法：新增明确不扫描/执行npuserv的运行档；SQL桥/约束/applier/包生命周期/并发恢复入手动CI；MSYS用pipefail保真实退出码。
  - 验收：精确SHA+run_id/attempt+环境+输入摘要+日志hash齐备；重新跑新提交不能借用旧c3a8538 run；不自动触发Actions。
- [ ] **H07｜整机并发长稳与性能门槛。组件：音视频真实链、资源采样。**
  - 方法：先定基线、时长和门槛；测RSS/MemAvailable/CMA、p95、帧/音频丢重乱序、队列、SQLite锁/WAL、日志封顶；不扩全局锁。
  - 验收：达到事先定义的真实负载/长稳标准，异常路径可解释；一次free快照不算验收。依赖E/F/G/H01。
- [ ] **H08｜切流、备份恢复与回滚演练。组件：部署manifest、停写窗口、数据版本。**
  - 方法：备份→停写→迁移→切流→健康→观察→允许恢复；处理切换后新数据，不以git分支回退代数据回滚。
  - 验收：现场演练能回到已定义兼容状态，无新数据静默丢失；签收版本/哈希/步骤/读数齐备。依赖B05/C05/H07。
- [ ] **H09｜文档和入口归一。组件：README、DEPLOY-CONTROL、VIDEO-DOMAIN与本清单。**
  - 方法：改过时“未提交/未连板/历史库未建/只剩上板”措辞；wiki只留入口与证据；核对旧规划缺页；补完整本地SOP与版本清单。
  - 验收：每个完成项有证据，所有链接可达，当前事实与历史快照区分；不重复维护第二套待办。

## 11. 执行顺序与完成一步的操作

首批A01/A02/A03/A04/A05解决真实阻断。接着B/C/D建立合法数据与生效协议，再接E/F/G真实消费，最后H生产交付。组内存在交叉依赖，允许重排，但不因“难”直接省略。

实施时固定五步：

1. 读取对应项及依赖，核对当前代码/现场，不以历史阶段总结当现势事实。
2. 在隔离工作区实现，只动授权路径；Qoder目录不碰。API/存储合同变化先回写决策。
3. 跑该项的有效验证，保存真实退出码与证据。危险域接口在测试数据/专用服务上跑。
4. 达到验收后将该条`[ ]`改`[x]`，在其下追加完成日期、SHA、日志/报告链接和局限；未满足的子能力另立待办。
5. 更新入口/必要阶段记录；是否提交推送按当次授权，wiki当前只本地。

本轮新增的是文档体系与指定视频源码审查，不实施上述未勾项。后续优先A01；没有解决指针可取性、单写者和安装路径之前，不应宣称部署就绪。
