# CI 参考项目与采用理由

检索日期：2026-11-05。以下结论来自实际读取的公开工作流，链接指向本次读取的分支；上游会继续变化，不把本页当作其永久版本清单。GitHub API 的本机直连多次超时，随后通过网页读取工具查阅目录和文件正文。没有取到可靠提交号的项目不填写猜测的 SHA。目录检查涵盖已列出的默认开发分支，未声称审计了上游所有历史分支。

## 实际查阅

| 项目 | 已读取的配置 | 已有做法 | 对本仓的借鉴与边界 |
|---|---|---|---|
| Home Assistant Core | [dev/ci.yaml](https://github.com/home-assistant/core/blob/dev/.github/workflows/ci.yaml) | `info` 按改动选择范围；`pytest-full` 按测试桶分片；`pytest-partial` 按集成选择；多种数据库服务测试；`hassfest`、生成文件校验；`benchmarks` 经 CodSpeed 分别测 simulation、memory；最终保留测试与覆盖报告 | 功能、兼容性和性能分开；失败保留证据。本仓规模较小，先串行执行测试，避免无收益的分片和模型多进程内存开销。该文件没有 SSH 到物理家居设备的检查，不能把 HA 的 CI 绿灯解释成实机验收。 |
| klar-ha-nlu | [main/ci.yml](https://github.com/FABBricate-IT-Solutions/klar-ha-nlu/blob/main/.github/workflows/ci.yml)、[language-parity.yml](https://github.com/FABBricate-IT-Solutions/klar-ha-nlu/blob/main/.github/workflows/language-parity.yml) | Rust 1.85 的 fmt/clippy；Python 与脚本自检；重新生成语言、parity、full-home 资料后检查工作树差异；`cargo nextest run --locked` 的不同档位；前端文件齐备时构建。语言对拍在周一与手动运行，执行 `parity_langs` 和 `full_home_quick` | 配置、测试与生成文件应保持一致；快速测试与较重对拍分层。本仓没有完整 klar Cargo workspace，集成官方二进制，不能虚构 `cargo test --manifest-path klar/Cargo.toml`。CI 中的 release-gates 文本提到评测，但这里不把一句 echo 当成独立性能报告的证据。 |
| Home Assistant intents | [main/ci.yaml](https://github.com/home-assistant/intents/blob/main/.github/workflows/ci.yaml) | 按 sentences/responses/tests 变更选择语言，核心变更全跑；`script/lint`、`script/test`、`intentfest validate`、`check_overmatch`、`prune --check`，以及 codeowners/transliterate 一致性检查 | 测试数据和配置一起版本化；参数化用例检查短路与路由；添加服务时要求契约检查。该文件未展示 ASR 模型速度、SSH 或真实麦克风测试。 |
| ESPHome | [dev/ci.yml](https://github.com/esphome/esphome/blob/dev/.github/workflows/ci.yml) | 主机 pytest；C++ 单测；组件配置验证与固件编译分开；按组件、平台及工具链选构建批次；主机集成测试；独立内存影响对比与 CodSpeed 基准；失败收集调试资料；汇总状态 | 构建成功、服务互通、实机行为分别留证；基线与候选在明确条件下比较；平台矩阵由数据声明。本次读取的工作流没有自动刷写并验证物理设备，不能照搬为“已上板”。 |
| xiaomusic，小爱音箱社区项目 | [main/ci.yml](https://github.com/hanxi/xiaomusic/blob/main/.github/workflows/ci.yml) | amd64/arm64/armv7 镜像构建，QEMU/Buildx；容器内用程序 `-h` 冒烟；镜像 tar 制品、Docker Hub 与版本发布 | 真正运行交付物，比只验源码更接近安装用户。该文件不是完整单元测试、模型精度或性能回归，不能由多架构构建推出语音功能可靠。另有 `fmt.yml`、`static.yml`，本页未将它们的未知内容计入结论。 |
| xiaogpt，小爱音箱社区项目 | [main/CI.yaml](https://github.com/yihong0618/xiaogpt/blob/main/.github/workflows/CI.yaml) | Python 3.12/3.13/3.14；PDM frozen lockfile；Black、lock 检查、CLI `--help`、`unittest discover`；测试成功后，非 PR 事件构建发布 amd64/arm64 镜像 | 解释器兼容性、锁文件、CLI 可启动和交付物各有检查。本仓使用板端 Python 3.8 与主机 Python 3.13 的矩阵，不照搬其依赖管理工具。该文件没有远程设备或性能基准。 |

xiaomusic 与 xiaogpt 是社区项目，不代表小米或小爱同学内部 CI。公开资料不足以描述商业后台的测试方法，因此没有编造“官方 SDK 的端到端 CI”。

## 落到 HA4FPAI 的选择

1. `ci.yml` 负责源码语法、无模型契约、现有自检、Linux worker 生命周期和报告。快速测试不随生产档切换去加载 ONNX 或请求云服务。
2. `lang-linux-package.yml` 保留现有 arm64/amd64 包入口，交付包的安装、升级/卸载及不带模型启动由原有容器测试负责；新增检查须在打包前通过。
3. `performance.yml` 在同一 runner、同一个测试工具、同一个配置下依次测基线和候选，保留原始采样、P50/P95 与报告。主机 mock 数据只说明编排层变化。
4. `device.yml` 经能到达板卡的受控 runner，通过 OpenSSH 检查真实部署；推理与长跑显式选择，连接、源文件、协议、结果、进程与内存分别检查。
5. `adapters.yml` 单列需要外部模型源码的适配器对拍。常规 CI 不依赖公开入口目前取不到的 ERNIE 源码；对拍入口使用父仓 gitlink 固定版本，有权限时单独运行，缺件明确失败。
6. 比较时先判正确性与环境可比性，再判速度。缺少数据、版本不明、配置变化与设备掉线都必须显示，不能折算成零耗时或成功跳过。

上游的 CodSpeed、覆盖 SaaS 和镜像发布都不是本仓使用 CI 的前提。默认测试与报表采用 Python 标准库，可在有 Python 的主机和板端复跑；可选模型 adapter 对拍另需 NumPy。GitHub Actions 按手动入口调度并保存制品。
