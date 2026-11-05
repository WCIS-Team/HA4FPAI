# HA4FPAI CI：运行、性能比较与维护

本仓 CI 覆盖 Python 编排层、HTTP/Wyoming 契约、独立 NPU worker、安装包和现有实机部署。入口配置在 `ci/config.json`；远程部署参数在 `ci/device.package.json` 或 `ci/device.example.json`。日常改参数通常只需要改这些数据文件；接口或架构改变时，再同步更新对应契约测试。

参考项目及实际采用理由见 [公开 CI 调查](ci-references.md)，逐项测试责任见 [失效模式清单](CI-FAILURE-MODES.md)，本次真正执行的检查和结果见 [验证记录](CI-VALIDATION.md)。

## 五个 Actions 入口

| Actions 名称 / 文件 | 触发条件 | 主要工作 | 输出 |
|---|---|---|---|
| `CI` / `.github/workflows/ci.yml` | 手动；由其他手动工作流复用调用 | Actions 语法与表达式；Python 3.8/3.13；无模型契约；切块、FSM；Linux worker 生命周期；短长跑与故障校准 | 每套日志、`results.json`、`junit.xml`、任务摘要、校准日志 |
| `Build lang Ubuntu 20.04 packages` / `lang-linux-package.yml` | 手动，可填写版本号 | 先复用完整源码 CI；锁定 wheel 闭包；arm64/amd64 打包；最终包静态校验；amd64 Ubuntu 20.04 容器生命周期 | `.deb`、安装 `.tar.gz`、SHA256、依赖审计和生命周期报告 |
| `Compare orchestration performance` / `performance.yml` | 手动 | 同一 runner 上依次测基线源码与候选源码，使用候选的同一套工具和配置；先检查正确性，再判回退 | 原始采样 JSON、Markdown 表、独立 HTML 图表 |
| `Verify external NPU adapters` / `adapters.yml` | 默认分支手动 | 从父仓 gitlink 读取固定提交；取得外部 ERNIE 源码后执行 core full 与两个 adapter 替身对拍 | 外部源码提交/文件指纹、测试日志、JUnit |
| `Check deployed device` / `device.yml` | 默认分支手动 | SSH 检查现有部署；health、benchmark、soak；必要进程、协议、部署指纹、资源和可选旧基线比较 | `device.json`、逐项摘要、性能比较 HTML/JSON |

五个入口都只在使用者手动执行时运行。push、PR、tag 推送和定时任务均不启动 Actions；仓库 Actions 功能保持开启，供手动使用。触发约定见 [AGENTS.md](../AGENTS.md)。

手动运行安装包任务时，会通过 `workflow_call` 先调用源码 CI。`CI required` 汇总任务会拒绝失败、取消或跳过的前置检查；它不自行启动检查。测试矩阵和包矩阵的 `max-parallel` 都是 1。

GitHub 保存 artifact 的期限有限；要长期留作基线，下载完整 JSON 和语料清单。JSON 是机器比较依据，HTML 用来阅读。报表的 `--output` 是文件名前缀；工具会拒绝覆盖本次读取的输入数据或配置文件，请使用不同前缀。

## 第一次启用

1. 将 CI 工具、固定语料、工作流及对应业务版本一起纳入可运行提交；推送前核对源码阶段、注册表与 fixture 一致，引用的新业务文件也已入库。默认分支上的手动入口需要这些文件已进入默认分支。
2. 在仓库 Actions 页面确认 `CI` 和安装包工作流可运行。源码和打包任务使用 GitHub 的 Ubuntu/Windows runner，不需要在板卡上安装构建工具。
3. 在 Actions 页选择工作流和分支，点击 **Run workflow** 后才开始。若配置分支保护中的 `CI required`，应预留人工执行步骤；未手动运行时不会产生检查结果。
4. 性能入口填 `baseline_ref`，例如优化前的完整提交号；留空使用默认分支。选择待测候选分支运行。
5. 实机任务需要下面的设备 runner 和 SSH 配置。也可先直接使用 `ci/remote.py`，无需注册 GitHub runner。

固定版本入口：checkout v4、setup-python v5、upload/download-artifact v4、actionlint 1.7.7；可选 adapter 集成的 NumPy 在 `ci/requirements-adapters.txt` 固定。板端 Python 3.8 的兼容要求保留在矩阵与语法检查中。

## 手动重跑

以下命令从仓库根目录执行。Windows 示例将临时结果放工作区外的 `workenv`；Linux 可改为专门的报告目录。常规档位只需 Python 标准库，可选 `adapters` 档需要 NumPy 和外部模型适配器源码。依赖和构建环境优先使用校机或已有隔离容器，勿在日常机另建整套环境，也不要在 113 板卡上编译。

```powershell
py -I -B -X utf8 ci/run.py --profile quick --output S:/work/WCIS/workenv/ha4fpai-ci/my-quick
py -I -B -X utf8 ci/run.py --profile validate --output S:/work/WCIS/workenv/ha4fpai-ci/my-validate
```

```sh
python3 -I -B ci/run.py --profile full --output /workenv/ha4fpai-ci/my-full
python3 -I -B ci/run.py --profile soak --output /workenv/ha4fpai-ci/my-soak
```

| 档位 | 内容 |
|---|---|
| `validate` | 加载 CI 配置；检查声明源码目录存在；按 Python 3.8 语法解析 `.py`，不生成 pycache |
| `quick` | validate + 契约测试 + 基础设施测试 + 现有切块/FSM 自检；不加载模型、不请求外部后端 |
| `full` | quick + Linux NPU supervisor 生命周期 + 主机短长跑 + 8 个定向故障变体校准；不依赖外部模型仓 |
| `adapters` | Linux full 的全部内容 + ERNIE/Qwen 两个 adapter 自检；外部源码、NumPy 和文件必须齐备 |
| `soak` | validate + 有界主机编排长跑；次数和内存门限来自配置 |

Windows full 把仅支持 Linux 的 NPU supervisor 套件明确记录为 `not-applicable`。`adapters` 要求 Linux；缺 NumPy、外部源码或测试文件会失败，不能据常规 full 推出模型适配器已验证。

### 外部适配器集成

当前 ERNIE 子模块公开地址/固定提交的读取返回 404，无法仅凭公开入口确认是访问权限、仓库状态还是提交可达性。常规 PR CI 不依赖它，也不持有它的读取令牌。已有本地源码的两个 adapter 自检已经单独实跑；其具体代码 SHA 与 Git 版本是否已知，记录在 `external_sources`。

要在 GitHub 执行外部对拍，建立 `adapter-ci` environment、限制到默认分支。若模型仓需要单独授权，配置只读该仓的 `ERNIE_SOURCE_TOKEN`；公开可访问时使用默认 `github.token`。`adapters.yml` 从 `ci/config.json` 的 `adapter_sources` 读取仓库和路径，从父仓实际 gitlink 读取提交号，再检出源码，跳过 LFS 自动下载。它不会追随外部 master 的最新提交。

已有环境手动准备源码后运行：

```sh
python3 -I -m pip install --only-binary=:all: -r ci/requirements-adapters.txt
python3 -I -B ci/run.py --profile adapters --output /workenv/ha4fpai-ci/my-adapters
```

需要初始化子模块时，按仓库已有读取权限取得父仓固定的 gitlink。源码不可读就是集成任务失败，不会转成成功跳过。没有正确初始化 Git 的本地副本，提交号记为未知并保留文件 SHA，不借用父目录 Git 提交冒充模型版本。

NumPy 用于替身的数组和 KV 几何检查，不加载 NPU 模型。常规 full 中，独立 worker 的正常加载、流式返回、独占冲突、错误及退出仍由不依赖外部模型的生命周期套件验证。

`ci/run.py` 为每套件单独启动进程，设置超时，串行执行并保留日志。Linux 超时清理进程组；Windows 使用进程树终止。JUnit 当前以套件为一个 testcase，详细断言和 unittest 方法数量在日志/JSON 中。

## 性能优化前后比较

常规代码优化使用 Actions 的 `performance.yml` 最方便：填优化前的提交号，选择候选分支运行。两份源码分别检出，工具、测试输入、参数、Python 和主机固定，然后按顺序测量。

本地也可在同一环境中使用同一工具测试两份源码：

```sh
python3 -I -B ci/bench.py --source /path/to/baseline --output /workenv/perf/baseline.json
python3 -I -B ci/bench.py --source /path/to/candidate --output /workenv/perf/candidate.json
python3 -I -B ci/report.py --baseline /workenv/perf/baseline.json --candidate /workenv/perf/candidate.json --output /workenv/perf/comparison
```

工具和配置来自所执行的 `ci/bench.py` 所在检出；`--source` 只选择被测业务代码。报告保存提交号（无 git 时为未知）、工作树状态、源码 SHA256、工具 SHA256、环境、条件、原始采样和正确性指纹。源码在测量期间发生变化会失败。

测量包括真实编排代码、音频重采样、切块和输出 WAV，但 ASR/LLM 使用 echo，TTS 使用 sine。每轮验证目标阶段实际执行、没有阶段错误、文本与切块有效、WAV 非空且采样率正确。它描述编排开销，不能作为语音识别精度或真实模型推理速度。

- 时间单位为毫秒，使用 `perf_counter_ns()`。
- 第一请求单独记录，不包含进程启动/import；正式采样先 warmup。
- 保存 P50、P95、均值、最小/最大、标准差及原始读数。P95 用 nearest rank；默认 60 个样本时取排序后的第 57 个值。最初 15 次采样在相同源码和条件下曾因单次波动触门，原始记录保留在验证记录中；样本增加用于降低分位数噪声，门限保持原值。
- 初始门限：chain P95 允许 15% 或 5 ms 的噪声；split P95 允许 20% 或 0.05 ms。只有恶化量超过两者中较大的值才失败。
- first-request 和进程 RSS 等未配置门限的数字只展示，不负责回退判定。
- 调整阈值属于修改验收条件，应保留理由；先确认正确性、后台负载与测量条件，再判断回退。

| 比较状态 | 含义 | 退出码 |
|---|---|---:|
| `passed` | 条件相同、数据完整，未超门限 | 0 |
| `no-baseline` | 只有候选测量；没有加速结论 | 0 |
| `regression` | 可比，但至少一项超过回退门限 | 1 |
| `invalid` | 正确性失败、样本/指标缺失、统计与原始样本不一致等 | 2 |
| `incomparable` | 主机、解释器、工具或工作负载等条件不同 | 2 |

不同 hosted runner 跨两次任务产生的 JSON 通常不可比；请让 `performance.yml` 在同一个 job 里重测两份源码。新增指标、修改工作量或升级工具后，重新测基线，旧报告继续保留。

### 同一轮比较参数取舍

`benchmark.variants` 可以声明多个参数组。默认 `split-12` 与 `split-20-16k` 使用相同输入、源码和 16 kHz 输出，切块上限分别是 12、20；`split-20` 的 48 kHz 输出另作覆盖。测完一次后：

```sh
python3 -I -B ci/experiment.py --measurement /workenv/perf/candidate.json --baseline-variant split-12 --candidate-variant split-20-16k --output /workenv/perf/parameters
```

Actions 可在 `baseline_variant`、`candidate_variant` 填同一对 ID。参数报告同时列出时延、参数、块数和输出帧数；文本必须守恒，输出采样率必须相同。配置改变被显式标记。这组数据用于评估参数取舍：sine TTS 的输出长度也随块数变化，所以需要一起看帧数，语音质量另由真实模型与语料验证。跨采样率的两组会返回 incomparable。

## 改参数、框架、后端时改哪里

| 改动 | 需要更新的位置 | 先跑什么 |
|---|---|---|
| 采样次数、warmup、回退门限 | `ci/config.json` 的 `benchmark` | quick，再 performance |
| 切块长度或输出采样率的试验组 | `benchmark.variants`；每个 ID 唯一；配置进入测量条件 | bench，检查每组原始读数和正确性，再比较同组的前后版本 |
| 生产 ASR/LLM/TTS 档位 | 生产配置按项目规程修改；quick 继续使用 `ci/fixtures/mock.yaml`；实机非凭据参数加入 manifest 指纹或实验条件 | quick + device health，再显式 benchmark |
| 新增后端档 | 注册表、生产实现、fixture 中需要启用的档、对应故障断言 | quick；`FM01` 会核对阶段、档名和注册表 |
| 新增/删除一个 chain stage | `Chain.STAGES`、注册表、fixture chain；更新正常/短路/降级契约和基准目标阶段 | quick + full + performance |
| 修改 RMS/KWS 的触发条件 | fixture/测试输入需能到达被测阶段；保留“确实执行该阶段”的断言 | `FM03`、`FM04`、校准 |
| 服务接口、JSON 键或业务码变化 | `test_contracts.py` 中 HTTP 契约；设备 manifest 的 `checks.expect`；有推理响应变化时更新 `device_probe.py` | quick + device health |
| 文件移动、架构模块拆分 | `source_roots`、`legacy_suites` 路径、子模块准备步骤及两个 adapter 测试路径 | validate + full |
| 新增服务/端口 | 设备 manifest 的 services/checks/process_patterns；协议不同则增加受限探针和替身测试 | infrastructure + health |
| 模型/云供应商/请求语料变化 | 新 deployment/workload 条件、语料 SHA、参考文本和门限；建立新基线 | health + benchmark |
| 打包文件布局/系统依赖变化 | `lang/packaging/linux/`、`ci/package_check.py` 的交付契约；源码检查目录 | 完整 package workflow |
| 历史 bug | 在失效清单增加条目，写独立期望值和最小回归用例 | quick/full 中适合的档位 |

测试配置不读取生产 `online.yaml`，不会随开发者默认档切换去加载模型或请求云服务。业务行为的预期确有改变时，同步改断言，并说明新的合法行为。`calibrate.py` 的变异定位若因重构失效，会明确失败；更新定位后确认原版仍绿、故障版仍红。

校准只修改独立副本；不会变异共享工作树或板端代码。它验证当前 8 种关键失效可以被检出，并不等于所有故障路径的穷举。

## 设备：两种部署清单

| 清单 | 源码目录 | 配置目录 | 用途 |
|---|---|---|---|
| `ci/device.package.json`（默认） | `/usr/lib/lang-server/lang` | `/etc/lang-server/config` | `.deb` 安装的 lang-server |
| `ci/device.example.json` | `/mnt/data/lang` | 同一根目录下 `config/` | 手工开发部署；需按实际安装位置调整 |

复制一份清单给自己的板卡，检查 `deployment_id`、source/configuration root、文件清单、必要进程、端口、Wyoming 能力和 workloads。source/config 文件只返回 SHA256，不回传内容；禁止指纹读取 `online.yaml` 和 `.env`。把所有相关的非凭据参数文件列全，未列入的外部配置和云模型变化仍属于测量限制。

已禁用的服务会显示 disabled，指定但不存在的文件/进程会失败，健康档的推理任务显示 not-run。开始与结束核对部署文件，变化或缺件会保留失败及其他已取得的证据。健康接口公开的后端档位也进入配置指纹；切换档位后需要显式允许参数试验。供应商在同一档内改变模型等外部漂移仍需另外声明实验条件。

手动命令使用已有 SSH alias/key/agent 和已验证的 known_hosts：

```powershell
py -I -B -X utf8 ci/remote.py --target board-ci --mode health --output S:/work/WCIS/workenv/ha4fpai-ci/board-health.json
py -I -B -X utf8 ci/remote.py --target board-ci --mode benchmark --allow-inference --output S:/work/WCIS/workenv/ha4fpai-ci/board-bench.json
py -I -B -X utf8 ci/remote.py --target board-ci --mode soak --allow-inference --output S:/work/WCIS/workenv/ha4fpai-ci/board-soak.json
```

可加 `--manifest`、`--known-hosts`、`--identity`、`--port`、`--corpus-root` 和 `--timeout`。连接强制 BatchMode 与 StrictHostKeyChecking；不使用口令登录，也不会自动接受新 host key。只上传本次独立探针到随机 `/tmp` 路径，manifest 经 stdin 传入；结束只移除这一个随机探针。

健康请求仅允许回环只读端点，不跟随 HTTP 重定向。实机推理仅允许 ASR/TTS，不执行对话动作链、开门、录制切换、登记、部署、安装、重启或模型下载。

### GitHub 设备 runner

1. 使用能通过 LAN 或已有 SSH 隧道到达板卡的 Linux 机器，安装独立 GitHub self-hosted runner，并标记 `ha4fpai-device`。Actions 设备入口使用 Linux 的 OpenSSH/临时文件权限；Windows 控制端可直接运行上面的 `remote.py`，复用已有密钥或 agent。
2. 建立 `device-ci` environment，限制到默认分支；按仓库管理要求设置审批人与 runner 访问范围。该 runner 不运行 fork PR 或普通源码 CI。
3. environment 变量 `HA4FPAI_SSH_TARGET` 填 host alias 或 `user@host`。alias、端口转发等由该 runner 的 OpenSSH 配置提供。
4. environment secrets：`HA4FPAI_SSH_KEY` 为测试账号专用私钥；`HA4FPAI_KNOWN_HOSTS` 为经其他可信途径核对的 host key 记录。私钥无交互口令；账号权限限制到所需只读/推理接口和临时探针文件。
5. 手动 dispatch 默认分支。任务先验证分支，再进入设备 runner；所有模式共用一个板卡锁，不取消正在测量的任务。

SSH 材料只写 runner 临时目录，权限设为 0600，结束清除；artifact 只收集独立的结果目录。没有把本机私钥、API 令牌或口令写入仓库。私钥/runner 的实际注册和配置由使用者在准备好的具体环境中完成。

### 真实推理、语料与基线

benchmark/soak 需要 `--allow-inference`，Actions 中需要勾选 `allow_inference`。如果后端是云 API，这些调用会发到当前部署选择的供应商，并可能计费。health 不发送音频或文本推理请求。

默认 ASR 样本是版本化的 `ci/fixtures/asr-zh.wav.b64`：2.472 秒短合成中文，参考文本、生成来源和 SHA256 在相邻的 `asr-zh.json`。固定测试句是“这是一条自动化测试。”；正常 CI 不重新生成音频。base64 文本方便随仓库保存，传输前解码为 WAV，再校验元信息和帧数据。旧 `a067.wav` 曾被项目记录判为非人声，已移出默认 CI 语料。

设备接收同一份有界 PCM16 单声道 WAV；本地验证头、帧数、采样率和完整数据，报告记录实际音频 SHA256 与输入时长。

默认 workload 通过 `corpus_metadata` 读取参考文本，并要求 `max_cer: 0.2`。也可给自己的 WAV 配 `expected_text` 与 `max_cer`；参考元信息的最小内容为 `schema_version: 1`、`text`、`sha256`。音频与元信息 SHA 不符会先在本地失败。

CER 为去掉空白后的字符编辑距离，保留标点和大小写。这个单句检查验证协议和识别内容的回归；模型总体精度需要另备覆盖口音、噪声和长度的人工语料。无参考文本的自定义任务只检查非空转写和业务成功，不输出识别精度结论。

设备报告包含端点时延、推理 P50/P95、RTF、可用的分阶段时延、PID/RSS/CPU ticks，以及冷态、全组 warmup 后和测量结束的资源。RSS 为边界采样，不能当成峰值；长跑增长量从全组 warmup 后计算，避免把首次初始化当作持续泄漏。PID 从冷态到结束都核对。指定窗口通过不代表全天稳定。

远程整体超时默认 benchmark/health 300 秒、soak 900 秒；可用 `--timeout` 修改。长跑次数、单次后端超时与整体截止时间应一起调整，超时会明确失败。

Actions 首次留空 `baseline_run_id`，保存候选报告；以后填同一模式的已成功 run ID。也可手动比较：

```sh
python3 -I -B ci/report.py --candidate /workenv/device/after.json --baseline /workenv/device/before.json --gates ci/device.package.json --output /workenv/device/comparison
```

当明确在试验板端参数改变时，可使用 `--allow-config-change`（Actions 对应勾选项）；报告写明配置哈希改变，其余设备、解释器、工具、语料和正确性要求仍成立。测量期间改部署依然失败。改变工作量、服务集合或模型条件后，建立新的基线。

## 安装包检查的范围

`ci/package_check.py` 检查最终 ar/tar 格式、架构、必需文件、逐文件校验和、Python 3.8 语法、配置脱敏、maintainer script 的可执行位/LF，以及安装压缩包是否真正包含同一个 deb。

`lang/packaging/linux/ci_test.py --package` 会实际安装、重装、remove、purge，只能在专用可丢弃 Ubuntu 20.04 容器中使用。它检查私有 venv、wheel 闭包、服务账号和四个 unit，并禁止健康冒烟过程加载模型或推理。每次生成唯一的用户数据 fixture，便于在保留证据的专用测试容器里重跑。

amd64 生命周期通过说明相同源码和依赖版本的安装过程通过；arm64 的静态检查说明交付结构与目标标签通过。arm64 真机的 systemd、麦克风、扬声器、NPU/CMA、外设和真实模型仍需单独验收。CI 不刷 BOOT，也不把容器、mock 或健康回显当作整机验收。

## 从失败报告定位

| 看到的失败 | 优先查看 |
|---|---|
| `CI required` 红 | 上游哪个 job failed/cancelled/skipped；对应 artifact 的 results、suite log |
| 配置/阶段新增后 quick 红 | FM01 的注册表/fixture；实际异常；不要随生产配置加载模型来绕过测试 |
| 注入没有到目标阶段 | stages_ms、RMS/唤醒输入、调用次数；确认请求没有在前级短路 |
| 校准红 | green 日志是否先通过；红变体是否真正运行 1 个目标方法；重构后注入字符串是否需要更新 |
| package 红 | metadata-audit、package-static、lifecycle.json 及容器日志 |
| 性能 invalid/incomparable | 正确性、原始样本数、harness/workload/环境哈希和指标集合 |
| 设备端口通过但部署指纹失败 | 清单选择了开发部署还是包部署；源码/configuration root 和进程路径 |
| 设备推理 not-run | health 模式，或前置健康/指纹/进程检查失败 |
| 推理返回 200 仍失败 | 业务码、stages_error、非空文本/WAV、参考转写；HTTP 200 不是业务成功 |
| soak 红 | PID 是否变化、边界 RSS 是否增长、是否有其他任务同时使用板卡 |

本仓没有接 CodSpeed 或覆盖 SaaS 的必需账户，也没有安装 Home Assistant 本体来冒充项目测试。公开参考做法只在适合本仓的地方采用；验证边界由实际执行证据决定。
