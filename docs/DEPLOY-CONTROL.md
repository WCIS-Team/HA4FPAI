# 控制服务部署与上板前清单（unified-control 分支）

> 状态：**部署准备就绪，未部署**。上板动作（拷文件、装单元、迁移数据）等她当次授权。
> 配套单元：`control/deploy/ha4fpai-control.service`、`control/deploy/ha4fpai-task-worker.service`。

## 一、板上布局（目标）

| 路径 | 内容 | 权限 |
|---|---|---|
| `/root/ha4fpai-control/` | `control/` 包整目录（纯标准库，无 venv 需求） | root |
| `/var/lib/ha4fpai-control/` | `business.sqlite3`＋WAL、`exports/`、`token`（0600）、`media/` | 服务用户可写 |
| `/var/log/ha4fpai-control.log` 等 | 日志（append，需 logrotate 或 journald 上限 drop-in，防爬盘——板上既有教训） | — |

数据落点遵循规划：数据盘（eMMC ext4，UUID 校验）优先；SD 只读兜底**不建库**（健康接口区分"存储不可用"与"空库"）。

## 二、安装步骤（上板日执行，每步留回执）

1. **核对版本**：板上现有服务清点（8091/8092/8093 视频、lang-server 0.1.1、klar 10520）；确认 `unified-control` 分支 commit 号并记录。
2. **传文件**：`scp -r control/ root@113:/root/ha4fpai-control/`（或经 107 中转），传输后 `sha256sum` 抽查 3 件。
3. **建数据目录**：`mkdir -p /var/lib/ha4fpai-control/{exports,media}`；确认挂载点 UUID 与 `board.json` 记录一致；`df` 留存可用空间读数。
4. **生成令牌**：板上 `python3 -c "import secrets;print(secrets.token_urlsafe(32))" > /var/lib/ha4fpai-control/token && chmod 600 /var/lib/ha4fpai-control/token`。
5. **装单元**：拷两个 `.service` → `daemon-reload` → `enable --now ha4fpai-control ha4fpai-task-worker`；`systemctl is-active` ＋ `/healthz` 200 ＋ 启动日志里 store_id/generation/epoch 三行留档。
6. **接现役入口**：给 executor / console 的启动环境加 `HA4FPAI_CONTROL_URL=http://127.0.0.1:18100`（单元 Environment= 或 drop-in）；PC 控制台访问板端时经 SSH 适配层转发（沿用 D-1793782374 的本机控制台模式）。**不配该变量＝旧路径，行为不变——这是回退开关**。
7. **迁移 dry-run**：`POST /v1/voiceprint/import-preview` 两份旧库路径 → 核对人数/冲突/错误报告 → `control/migrate.py`（阶段02 交付）正式导入（activate=False 隔离）→ 人工核对后再激活。
8. **冒烟**：`POST /v1/commands`（system.time）→ succeeded；`/bridge/text`「现在几点」→ 可念回复；「开始录制」→ `video_unreachable` 或按板端实际服务状态返回——**视频动作在板端验收（验收10）前一律不宣称可用**。

## 三、回退

- 关统一路径：去掉 `HA4FPAI_CONTROL_URL` 环境变量重启 executor/console → 旧双通道原样恢复。
- 停控制服务：`systemctl disable --now ha4fpai-control ha4fpai-task-worker`；数据目录保留（库是权威，不删）。
- 回滚代码：板上目录整目录改名保留证据，恢复旧文件；数据库 schema 由 migration 版本管理，**降级需按迁移方案的反向转换流程，不得直接用旧程序开新库**。

## 四、上板前检查清单（每项要有证据才算勾）

- [ ] 分支全部测试绿：`smoke.py` ＋ `test_video_adapter.py` ＋ `test_migrate.py`（本机 py3.13）
- [ ] py3.8 实测：**未做**——上板日第 5 步启动成功即 first 证据；`py_compile` 全部 control 文件在板上跑一遍
- [ ] 内存预算：两单元 RSS 采样（预期 control<60MB／worker<50MB）， MemAvailable 快照留档（验收15 基线的一部分）
- [ ] 端口核对：18100/18101 未与现役冲突（8091-8093、10300-10302、10520/10521、18091、10900、7861、2046、18080）
- [ ] 日志封顶：journald 上限 drop-in 或 logrotate 已确认覆盖两个新日志
- [ ] 令牌权限：`/var/lib/ha4fpai-control/token` 0600 且不进任何仓库/日志
- [ ] 视频域状态：`/v1/capabilities` 的 `board_verified=false` 如实展示，界面不标可用
