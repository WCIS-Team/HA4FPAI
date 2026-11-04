# CI 验证记录

日期：2026-11-05，本机时区。原始 JSON 的 UTC 时间处于 2026-11-04 晚间。动态工作流：`dwfrun-4010afc1-1814-4c3b-8884-f0bc75943e44`。

本页记录初次本地验证窗口的结果，不替代 GitHub Actions 的运行记录。使用和扩展方法见 [CI.md](CI.md)，覆盖范围见 [失效模式](CI-FAILURE-MODES.md)。该验证窗口没有提交、推送、发布 Release、注册 runner 或设置 GitHub secrets。

发布配置已按用户后续要求统一为手动执行：push、PR、定时和 tag 推送均不触发工作流，打包任务仅在手动运行后复用核心 CI。发布提交使用 `[skip ci]`，不会通过推送来验证 GitHub 运行。

## 已执行

本机证据根目录：`S:/work/WCIS/workenv/ha4fpai-ci/`。最终文件/证据审计为 `delivery-audit.json`，数值汇总为 `delivery-facts-final.json`。这些文件保留在工作站，未将整个临时区打入代码仓库；GitHub 后续运行的证据由各 job artifact 保存。

| 检查 | 实际环境与内容 | 结果 | 证据相对根目录 |
|---|---|---|---|
| Actions 静态检查 | 校机 Windows，官方 actionlint 1.7.7，发行 ZIP 与官方校验和核对；检查表达式、依赖和工作流语法，shellcheck/pyflakes 关闭 | 首次发现并修正一处 YAML 冒号解析错误；随后通过。新增 adapter 入口后的完整重查见最终 actionlint 记录 | `actionlint-publish/actionlint.json`；最终编码/工作流清单见交付审计 |
| Core full | 现有 Linux 容器，Python 3.8.10；无 NumPy、无模型源码依赖；8 个检查组，含 18 个 unittest 方法及切块/FSM/worker 自检 | passed | `core-full-delivery/reports/results.json`、同目录日志/JUnit |
| 外部适配器集成 | 另一现有 Linux 容器，Python 3.8.10 + 已有 NumPy + 已有模型适配器源码；10 个检查组 | passed；外部 Git 版本未知，保存实测文件 SHA | `adapters-release/reports/results.json` |
| Windows quick | Windows，Python 3.13.11；契约、基础设施、切块、FSM | passed | `windows-quick-delivery/results.json` |
| 故障红绿校准 | 原版契约/基础设施先通过，再逐个运行隔离故障副本 | 8/8 变体被检出 | `core-full-delivery/reports/calibration.json`、`calibration-red-FM*.log` |
| 主机性能流程 | 同一 Linux 容器、相同源码/条件，3 个参数组，每组 warmup 5、正式采样 60；先正确性再计时 | A/A passed；这轮没有进行业务代码优化 | 原始数据在 `performance-linux-release/`；最终比较在 `report-safety/comparison.*` |
| 性能判定校准 | 将副本原始时延构造为明确退化；另构造不同设备条件；另测无基线与输入同名保护 | 分别返回 regression/exit 1、incomparable/exit 2、no-baseline/exit 0；输入碰撞 exit 2 且数据未变 | `report-safety/slower-input.json`、`slower-report.*`、`different-input.json`、`different-report.*`、`no-baseline.*`、`checks.json` |
| 参数试验 | 同一候选测量中的 `split-12` 与 `split-20-16k`；同输入/源码/输出采样率，保留块数和帧数差异 | passed | `report-safety/parameters.*` |
| arm64/amd64 最终包 | 复用既有已审计 wheelhouse，各构建一个 deb 与安装 tar.gz；每包 78 文件、28 Python 文件、28 wheels | 两种架构静态检查通过 | `package-acceptance/static-arm64.json`、`static-amd64.json` |
| amd64 包生命周期 | 专用 Ubuntu 20.04 测试容器；实际 install、reinstall、remove、purge；保留用户配置与假模型缓存；不推理 | 14 项通过，最终 package purged，用户 fixture 保留 | `package-acceptance/lifecycle.json` |
| 实机语料/接口 | `U.FMSH`、aarch64、Python 3.8.10、kernel 5.4.52；既有包部署，HTTP 与三个 Wyoming describe；ASR/TTS 各 warmup 1 + 采样 5 | passed；5 次正式 ASR 的 CER 都是 0；TTS 非空、非静音 PCM16 | `device-delivery/benchmark.json`、`benchmark-report.*` |
| 实机短窗长跑 | 相同设备与语料，另一个明确记录的短窗 manifest：warmup 1、每项 3 次测量 | passed；PID 未变，warmup 后 RSS 增长 0 KiB | `device-delivery/short-soak.json`、`short-soak-manifest.json`、`soak-report.*` |

原始校机下载在一次最终重查时连接重置。恢复检查使用之前已下载且再次匹配 SHA256 的同版 actionlint；保留失败记录，不将网络失败解释成配置通过。

## 实机这次测了什么

默认语料是 [asr-zh.json](../ci/fixtures/asr-zh.json) 所描述的 2.472 秒合成中文，固定句子“这是一条自动化测试。”，WAV SHA256 为 `165f8fdbb2eccae5ed342b94d9ac95c4fd14fe84d05899fbd77e3a83a1f1947f`。音频以相邻的 base64 文本随仓保存。它验证单句内容契约，不能估计整个中文 ASR 的精度。

| 接口 | 正式样本 | P50 | P95 | 其他观察 |
|---|---:|---:|---:|---|
| `/api/v1/asr` | 5 | 1617.55 ms | 1789.70 ms | 5 次 CER=0；RTF P50 0.654 |
| `/tts` | 5 | 1895.09 ms | 2233.55 ms | PCM16 非空且非静音；RTF P50 0.767 |

这组 ASR 使用 `remote-api`、TTS 使用 `edge`，数字包含既有服务、云 API 与网络的影响。没有运行 LLM 对话、klar 动作执行、开门、录制切换、登记、部署或重启。

**板上既有部署与本次候选工作树不是同一版。** 实机 `server/core.py` SHA 为 `2671f59e…`，候选为 `1295cb39…`，完整值在各自 JSON 中。这次没有把新 deb 安装到 113；实机结果只归属于报告记录的部署指纹，不能当作候选新链已上板。

短窗只采集全组 warmup 后到结束的 RSS 增长；同时保留冷态和 warmup 后的值。0 KiB 只说明这三次测量的边界读数，没有小时级稳定性或原生分配器无泄漏的结论。

## 性能报告的解释

本次 Linux A/A 两份业务源码 SHA 与条件完全相同，检查目的是验证采样、可比性和回退判定。相同源码仍出现不同读数，某些 P95 甚至下降较多；这些差异不能作为代码加速结论。

早先的 Windows A/A 原始记录也保留：

- 15 次采样时，`split-12.chain_ms.p95` 从 33.1872 到 39.6727 ms，触发门限。
- 提高到 60 次后，另一轮仍从 32.9106 到 38.8719 ms，触发门限。
- 默认 warmup/samples 调为 5/60，**15%/5 ms 等门限未放宽**。最终 Linux 流程通过，但共享桌面的频率、负载和调度波动仍存在。

相应目录是 `performance-acceptance/`、`performance-final/`。正式把性能作为阻断条件之前，应在稳定 runner 上多轮校准。当前性能与源码功能检查都是独立的手动入口，墙钟噪声不会自动触发或阻断其他检查。

参数报告同时保留输入、文本守恒、采样率、块数和输出帧数。sine TTS 的块数改变会改变总输出长度，数据用于参数取舍，不是语音质量评测。

## 本次修正过的问题

1. 初版故障测试音频过短，被 RMS 的最短有声时间提前挡住。改为能到目标阶段的输入，并显式断言 KWS 被执行、后级被跳过，之后完成红绿校准。
2. 比较器原先没有核对原始样本长度；空 WAV 也能进入语料。新增断言先失败，修正后通过，并补充摘要与原始样本一致性检查。
3. 两个旧 adapter 自检仍指向 `lang/llm/<model>/`，已更新为当前 `lang/llm/models/<model>/`。
4. 开发部署清单指向 `/mnt/data/lang`，而实机当前是包部署。保留最初“文件缺失但健康接口通过”的完整记录，再新增 source/configuration root 分离的包部署清单。
5. `a067.wav` 在旧项目记录中已判为非人声；初轮以其做非空性测量，不能支撑识别内容。已移出默认语料，换成固定短句、SHA 元信息和参考转写校验。旧记录只保留作调用/探针调试证据，不作默认模型基线。
6. 公开读取 ERNIE 固定提交、adapter 和 tokenizer 都返回 404；不能据此断言仓库或文件不存在。常规 CI 已不依赖该入口；需要外部源码的对拍单列为受控任务。
7. 打包生命周期原来使用固定保留 fixture 名，再跑同一专用容器会冲突。改成每轮唯一名称，已用实际安装/重装/卸载验证。

8. 旧 worker 自检只把退出后的残留 PID 打为 NOTE。已改成失败条件，并区分已释放资源的僵尸；补跑 38 项通过。在独立进程的内存副本中注入一个真实存活 PID 的观测，退出码确实为 1，共享源码 SHA 不变。证据在 `lifecycle-final/result.json` 与 `lifecycle-final/residual-calibration.json`。

9. 报表原先允许前缀与输入 JSON 同名，会覆盖输入。比较与参数试验入口现在会拒绝这种组合；回归测试及真实 CLI 验证均确认退出码 2、输入 SHA 不变。公开后端档位也加入配置指纹，模拟换档会判不可比，显式允许配置试验后才继续。

## 未执行和边界

- GitHub 上真正的 push/PR/schedule/reusable 运行没有执行；本次未提交或推送。所有 YAML 已用 actionlint 检查，实际脚本分别在以上环境运行。
- GitHub Ubuntu 24.04 + Python 3.13 的精确矩阵，以及该环境下固定 NumPy 2.2.6 的外部 adapter job，尚未在 GitHub runner 执行。本地验证为 Linux Python 3.8.10 与 Windows Python 3.13.11。
- 未读取 GitHub 用户凭据或配置外部源码权限。`adapter-ci` 的固定提交检出还需实际读取权限；本地 adapter 集成记录文件 SHA，Git 版本为未知，不将其冒充已验证 gitlink。
- 没有注册 GitHub self-hosted device runner、设置 `device-ci` secrets、修改分支保护；启用方法在 CI.md。Windows 手动 SSH 控制端已实际执行，Actions 的设备入口要求 Linux runner。
- 未把新 arm64 deb 安装到板上；amd64 容器生命周期与 arm64 静态检查分别留证，不能相互替代。
- 未进行小时级 soak、断电/存储破坏、原生 C++ allocator 穷举、模型 ASan 或真实 OOM 杀进程；MemoryError 是 Python 后端边界注入。
- 未验收物理麦克风、扬声器、HDMI、门锁、NPU/CMA 共存或完整 S1–S5 双通路，不提高整机完成等级或任务卡完成率。
- Wyoming 这轮检查的是 describe/info 能力；真实 Wyoming 音频流、实录唤醒和浏览器 UI 端到端行为没有由这份报告证明。

实际修改入口是 `ci/`、五份工作流、四份 CI 文档与 README 链接；现有测试的修改限于两个 adapter 路径、包测试 fixture 的可重跑性和 worker 残留进程失败门。共享工作树中原有其他功能改动不在这次提交范围，本次也没有执行任何提交。验证对象为当时工作树及报告指纹；推送前应与对应业务版本整合，审计会列出尚未跟踪的运行源码，便于人工核对。
