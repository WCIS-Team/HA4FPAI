# 控制服务部署与上板前清单（unified-control 分支）

> 状态（2026-11-07 更新）：[重构总清单](REFACTOR-CHECKLIST.md)是实施状态正本，**只以其中复选框为准**。已完成并测试的底座：A01/A02/A03/A05（指针链、单写调度、deb/unit 路径、PRAGMA 门禁）、B01–B06（约束迁移/备份恢复/WAL 运维）、C01–C04（JSON1 合同、暂存桥、幂等导入、模型审计）、D01–D06（设置分类/回执绑定/原子 applier/受限重启/统一写入口/recorder 回执）、E02/E04（extract-only、融合授权）、F02/F05/F06（持久回执、媒体真实性、Range/鉴权）、G02/G03（语音多轮、读模型）、H02/H06（挂载 guard、no-npu CI+证据）。
> **仍未就绪、不可宣布上板完成**：A04（初测副作用对账）、C05/C06（生产资料停写导入/投影消费）、E01/E03、F01/F03/F04、G01/G04、H01（真实安装/开机恢复）、H04（deb 干净环境生命周期）、H05（真件质量/打包）、H07（长稳）、H08（切流演练）——这些均需连板或生产数据，按清单逐项验收后才可动部署。
> 原 unified-control 已合主线并删名，旧主线归档在 archive-2026-11-06。配套单元：`control/deploy/ha4fpai-control.service`（内置调度）与退役的 `ha4fpai-task-worker.service`（仅手工 `--once`）。按 A02/A03 已完成的布局安装，但 H01 真实安装/开机恢复未验收前不宣布生产就绪。

## 一、板上布局（目标）

| 路径 | 内容 | 权限 |
|---|---|---|
| `/root/ha4fpai-control/` | `control/` 包整目录（纯标准库，无 venv 需求） | root |
| `/var/lib/ha4fpai-control/` | `business.sqlite3`＋WAL、`exports/`、`token`（0600）、`media/` | 服务用户可写 |
| `/var/log/ha4fpai-control.log` 等 | 日志（append，需 logrotate 或 journald 上限 drop-in，防爬盘——板上既有教训） | — |

数据落点遵循规划：数据盘（eMMC ext4，UUID 校验）优先；SD 只读兜底**不建库**（健康接口区分"存储不可用"与"空库"）。

## 二、安装步骤（上板日执行，每步留回执）

> **deb 安装（推荐，2026-11-05 起可用）**：`control/packaging/build_control_deb.py` 产出
> `ha4fpai-control_<版本>_arm64.deb`（纯 Python 组装，无 wheelhouse 依赖，Windows/容器均可跑）；
> postinst 自动建 `/var/lib/ha4fpai-control/` 并生成 0600 令牌（已存在则不覆盖）；
> 单元**不自动启用**，装完审阅后手动 `systemctl enable --now`（仓约定 Actions/自启全手动）。
> 走 deb 时下面第 2/4 步由包接管，第 3 步仍需人工核对数据挂载 UUID。

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

## 本地测试与打包（不走 Actions；按仓约定全部手动）

```powershell
# 1) 四套测试（本机 py3.13 全绿为准；py3.8 上板日实测）
python -B -X utf8 control\tests\smoke.py
python -B -X utf8 control\tests\test_video_adapter.py
python -B -X utf8 control\tests\test_migrate.py
python -B -X utf8 control\tests\test_packaging.py

# 2) 本地打 deb（产物默认进 workenv\ha4fpai-control-package\dist\，不入库）
python -B -X utf8 control\packaging\build_control_deb.py --architecture arm64 --version 0.1.0+20261105
```

lang 的 deb 打包沿用 `lang/packaging/linux/build_deb.py`（master/CI 同款，本分支未改动）。
本仓约定 **Actions 只手动运行**：以上全部为本地手动步骤，push/打 tag 不触发任何构建。

## 四、上板前检查清单（每项要有证据才算勾）

- [ ] 分支全部测试绿：`smoke.py` ＋ `test_video_adapter.py` ＋ `test_migrate.py` ＋ `test_packaging.py`（本机 py3.13）
- [ ] py3.8 实测：**未做**——上板日第 5 步启动成功即 first 证据；`py_compile` 全部 control 文件在板上跑一遍
- [ ] 内存预算：两单元 RSS 采样（预期 control<60MB／worker<50MB）， MemAvailable 快照留档（验收15 基线的一部分）
- [ ] 端口核对：18100/18101 未与现役冲突（8091-8093、10300-10302、10520/10521、18091、10900、7861、2046、18080）
- [ ] 日志封顶：journald 上限 drop-in 或 logrotate 已确认覆盖两个新日志
- [ ] 令牌权限：`/var/lib/ha4fpai-control/token` 0600 且不进任何仓库/日志
- [ ] 视频域状态：`/v1/capabilities` 的 `board_verified=false` 如实展示，界面不标可用
