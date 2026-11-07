# ha-small

基于官方 **Home Assistant Core 2026.10.4** 源码的删除式裁剪实验。

**归属：这是 HA4FPAI 的 `ha-small` 分支材料，不是独立仓库。** 当前只在本机 `repos/ha-small` 暂存（该路径被 `repos/.gitignore` 忽略），已于 2026-11-07 在 HA4FPAI 建立本地分支 `ha-small`（全量裁剪树，未推送）；本目录继续作为暂存与验证工作区。

目标是保留主要家庭控制能力，移除无关集成；不是重写HA，也不照搬HA4FPAI控制层的数据库/组件实现。

**当前：119个集成依赖闭包；保留5909个上游文件；上游文件修改数0。** 功能清单与每步勾选见[ha-cut](ha-cut.md)，来源见[UPSTREAM](UPSTREAM.md)，例外与补丁见[PATCHES](PATCHES.md)。上游[README](README.rst)和[Apache-2.0许可证](LICENSE.md)原样保留。

## 主要保留什么

- 状态机、事件总线、服务调用、设备/实体/区域注册与配置。
- 脚本、自动化、场景、模板、计时器、计数器和输入辅助实体。
- 用户认证、REST、WebSocket、前端/Lovelace、人员与区域。
- Recorder/SQLite历史与日志。
- Assist文本对话、语音pipeline、STT/TTS接口、Wyoming接入代码。
- 通用设备域、摄像头/流媒体/媒体播放器、MQTT、REST命令等扩展接口。

这些能力沿用HA原实现。HA原生person/recorder并不等于HA4FPAI的融合人脸声纹登记库；后者若需要仍通过明确的外部业务接口接入，不能仅靠删除自动获得。

## 本次实际结果

| 口径 | 实测 |
|---|---:|
| 官方sdist全部文件字节 | 235925308 B |
| 保留上游文件字节 | 38487103 B，源码减少约83.7% |
| 集成目录 | 1523 → 119，移除1404个 |
| 上游文件 | 保留5909；删除49275；修改0 |
| 裁剪源码压缩包 | 6251537 B；不含完整Python环境和外部前端包 |
| Linux功能验证 | 30/30，通过真实HA HTTP/API与服务执行 |
| 正常进程重启验证 | 4/4，认证/辅助状态/历史/SQLite完整性 |
| 测试venv | 672916 KiB，约657 MiB；包含外部前端、核心与动态依赖 |
| HA进程RSS快照 | 226.1 MiB；不是峰值、长稳或板端数据 |

测试环境为本机Podman隔离Linux x86_64、Python3.14.10、SQLite3.46.1。没有替换板上服务，没有运行或修改HA4FPAI/npuserv，没有推GitHub。

## 为什么保留119个而不是只有功能列表里的那些

保留集合由功能种子、manifest依赖/after_dependencies、绝对与相对静态导入递归计算，并补齐HA基础实体域。某些云、发现、默认配置模块被核心或保留模块引用，因此保留源码以避免改动上游。**源码存在不等于启用**：示例不使用`default_config:`，已测未加载默认整套服务。

安装保留上游pyproject的48项直接依赖；没有为了减少依赖而改核心import。外部frontend包也未擅自切碎。完整环境因此远大于裁剪源码：这不是“6MB就能部署HA”的方案。

## 如何运行

需要Linux与Python **>=3.14.2**。板端现有3.8.10不满足条件；本项目不提供假降级或解释器版本绕过。先在独立环境跑，勿覆盖已有HA配置。

```bash
python3.14 -m venv .venv
.venv/bin/pip install .
# 在测试过的Linux x86_64环境重现全部依赖，可使用：
.venv/bin/pip install -r cut/requirements-linux-x86_64.lock
# 此处为独立实验目录，不能指向已有HA生产数据。
mkdir -p /tmp/ha-small-lab
cp examples/configuration.yaml /tmp/ha-small-lab/configuration.yaml
.venv/bin/hass -c /tmp/ha-small-lab
```

说明：`examples/configuration.yaml`包含实验用helper、script、automation、scene及本地REST echo地址。平常使用可按需要删掉这些测试实体；不要补回`default_config:`后又把由此启用的功能当成最小档。

本次实际Linux镜像需额外系统库`libturbojpeg0`、`ffmpeg`。cp314语音依赖`pyspeex-noise`、`pymicro-vad`无可用轮子时，在**本机隔离容器**使用g++/make/cmake/pkg-config/libspeexdsp-dev构建，未改依赖源码、未在板子编译。锁文件仅代表已测Linux x86_64，不保证ARM64轮子齐全。

## 本机测试实例与安全边界

本轮容器名`ha-small-dev`，仅映射本机`127.0.0.1:18123`，容器内配置目录`/runtime`。测试结束已停止容器以释放资源，镜像、容器文件与测试数据保留；重新启动容器后还需显式启动`/venv/bin/hass -c /runtime`。自动测试通过真实onboarding创建随机测试密码；令牌只存容器测试目录，不放仓库、不打印。此实例是测试账户，不作为生产部署交付。

功能探针位于`cut/functional_probe.py`，只允许带`/runtime/HA_SMALL_TEST_ONLY`标记的隔离测试目录运行；会切换测试实体、注册测试owner、调用测试REST端点。不要拿去对生产HA执行。重启探针见`cut/persistence_probe.py`。

```bash
python cut/verify_source.py
# 以下只在按上述布局构造的隔离测试容器内执行：
python cut/functional_probe.py
# 先正常停止并重新启动同一个HA配置，再执行：
python cut/persistence_probe.py
```

本轮原始结果、依赖安装/运行日志及删除文件全表在`S:/work/WCIS/workenv/ha-small/`；复现步骤见[本地复现](cut/REPRODUCE.md)。主要未验项：真实音频设备/Wyoming服务器、MQTT broker、真实摄像头与录像、ARM64部署与并发长稳。逐项状态见[ha-cut](ha-cut.md)。
