# CI 失效模式与验证边界

清单是测试扩展入口。新增服务、参数或存储文件，先在这里补一行，再把它绑定到 `ci/tests/` 用例、既有自检或明确的人工验收。注入校准的前置条件是：正常代码通过，隔离副本中的故障变体被对应测试检出；不在共享工作树或实机生产进程上修改故障代码。

## 逐项归属

| 编号 | 失效机制与触发 | 合法结果与必须观察的内容 | 注入/验证入口 | 档位 |
|---|---|---|---|---|
| FM01 | 配置覆盖顺序变化、中文路由键或引号内容丢失、阶段注册表和配置脱节 | 字符串保持原义；每个启用档在注册表存在；测试不会随生产配置加载模型 | `test_contracts.Contracts.test_FM01_*`，语法/配置校验 | quick |
| FM02 | 采样率、PCM、块长度参数更改导致音频损坏、文本丢失或 TTS 超限 | PCM 往返误差有界；严格切块不超限；文本守恒；同时覆盖多采样率和多块长 | `test_FM02_*`；已有 `lang/split/selftest.py` 的不变量与固定种子生成用例 | quick |
| FM03 | RMS/KWS 短路失效，未唤醒仍跑 ASR/LLM | 静音不调用 KWS；KWS 未命中不调用 ASR/TTS；测试必须证实目标阶段确实被执行或被跳过 | `test_FM03_*`；改变 KWS 分支的隔离变异 | quick |
| FM04 | 网络超时、I/O 异常、分配错误；恢复后仍失败；LLM 和 TTS 同时失效 | 允许阶段明确报错并降级；不允许假装无错误；保留可用文本；没有音频就不伪造音频；准入计数回零 | `test_FM04_*` 的 TimeoutError/OSError/MemoryError × ASR/LLM/TTS × 瞬态/持续；复合故障 | quick |
| FM05 | 并发上限、排队超时和请求异常释放失效 | 满载返回明确 429 业务码；异常退出释放槽位；后续请求可继续 | `test_FM05_*`；真实 worker 并发/生命周期交给 NPU 自检 | quick/full |
| FM06 | 服务协议漂移、错误路由返回 200、兼容 API 空转、WAV 为空 | JSON 语义、业务码、阶段错误和音频头/帧数都验证；未知地址 404、缺文本 400；兼容 ASR 接口产生非空结果 | `test_FM06_*` 启动临时回环 HTTP server | quick |
| FM07 | 执行器 dry-run 仍执行；多服务只成功一个就报整体正常 | dry-run 没有 HTTP 副作用；任一必要服务掉线/503 就不能报全部成功 | `test_FM07_*`；HTTP 仅替身，录制/开门/重启不在实机自动执行 | quick |
| FM08 | 报告忽略缺失样本、不同环境乱比、零基线除零、降速仍报优化 | 缺项/条件不同明确 invalid/incomparable；超出相对和绝对双阈值才判退化；没有基线不声称加速 | `test_infrastructure`；`ci/report.py` | quick |
| FM09 | 健康返回 HTML 或字段类型变了仍判通过；探针误触发动作；SSH 不验证身份 | 必须是符合字段契约的 JSON；只读/推理端点白名单；连接失败明确失败；固定 host key；不重试非幂等动作 | `test_infrastructure`；`device_probe.py`；实机 health 档 | quick/device |
| FM10 | 音频语料格式错误、参考文本错误、主机/板端输入不同 | 只接受有界 PCM16 单声道 WAV；SHA 与时长进入条件；CER 使用非空人工参考文本；无参考只报告速度和非空性，不冒称精度 | `test_infrastructure`；`remote.prepare`；实机 benchmark/soak | quick/device |
| FM11 | 独立 worker 崩溃、独占槽位冲突、退出后残留、mock 与真实生命周期不同 | mock worker 自退后 supervisor 能收敛；退出后仍有存活 worker 必须失败，已释放资源的僵尸不算存活；只读实机监测另核对 PID/RSS | 原有 `lang/npuserv/tests/lifecycle_check.py`；两个 adapter stub 在外部源码集成档；残留 PID 观测做隔离负例校准 | full/Linux、adapters |
| FM12 | FSM 超时、打断、非法迁移和历史丢失 | 只发生合法迁移；错误与超时能复位；历史有界 | 原有 `lang/server/tests/test_fsm.py` | quick |
| FM13 | 包缺依赖、目标架构错误、安装行为错误、带入开发机配置、卸载遗留 | 锁定并审计 wheels；包元数据正确；容器里测最终 `.deb` 的安装/升级/卸载；无模型服务可启动 | 原有 `lang-linux-package.yml` / `lang/packaging/linux/ci_test.py`，前置新增 CI | package |
| FM14 | 优化绕过工作量、请求失败反而变快、对比的不是同一份部署 | 先正确性再计时；源文件哈希、工作负载、工具、设备和配置可追溯；失败测量不能比较 | `bench.py`、`report.py`、`remote.py`；原始 JSON 和 HTML | performance/device |
| FM15 | 长跑内存增长、服务重启被忽略、服务根本没部署 | 记录进程与 RSS；重启和必要文件缺失明确失败；时间/次数有界；配置变化不可混在一轮结果里 | `device_probe.py --mode soak`；主机 soak 检查 | soak/device |

## 状态与文件家族

- CI 的配置与语料：`ci/config.json`、`ci/fixtures/`、`ci/device.package.json` / `ci/device.example.json`、用户新增语料及参考转写。它们随代码审查，不从生产密钥配置生成测试期望值。
- CI 报告：每次单独输出目录，JSON 是机器读数，JUnit/日志用于定位，Markdown/HTML 用于比较。`common.write_json` 先写同目录临时文件再替换，避免把半份报告当有效基线；不存在完整报告就判缺席。
- 业务持久化：录音、图库、声纹库、模型和 NPU 状态不由常规 CI 改写。生产目录、在线凭证与真实设备状态不能从 mock 通过推出。

## 八类风险轴与明确不测

| 风险类 | 本次覆盖 | 本次边界 |
|---|---|---|
| 失控写入/崩溃 | worker 子进程崩溃；报告原子替换；最终包生命周期 | 不对 SD/eMMC 进行断电或扇区破坏；不声称覆盖硬件掉电持久性 |
| 并发与锁 | 准入闸、异常释放、单槽 worker；远程 CI 全局板卡锁 | 进程内闸不代表不同生产进程共享一个锁；真实 NPU/CMA 交接另验 |
| sync/flush/I/O | 后端边界注入 OSError；包文件完整性 | Python 报告没有事务性存储承诺，不把原子 rename 说成掉电保证 |
| 硬件和底层设施 | 架构/源文件/服务/PID/RSS/协议检查；实机选跑 | 不刷写 BOOT，不重启系统，不自动开门、登记身份或改变录制状态 |
| 同地址空间破坏 | 边界注入 MemoryError，独立 worker 生命周期 | 不等价于 C++ ORT 内部 allocator 遍历、ASan 或真实内核 OOM；此项保留为模型原生库专项 |
| 环境非标 | Python 3.8/3.13、Linux/Windows 的受支持测试；真实板端元信息 | QEMU/容器结果不等于 Cortex-A53 的模型速度；云服务不可冻结的变化在报告中提示 |
| 配置自伤 | 配置、注册表、类型、样本数量、工作量与探针限制 | 阈值是工程初始值，需用稳定多轮实测校准，不能当产品 SLO |
| 历史 bug/自身错误 | 固定种子、旧 selftest、红绿校准；每个新 bug 绑定编号 | 不用已有“自检通过次数”替代当前版本的实测报告 |

长跑只检查指定窗口的稳定性；没有通过小时级现场测试时不宣称全天无人值守。性能通过也不替代真实麦克风、扬声器、HDMI、NPU 及安全门禁的完整验收。
