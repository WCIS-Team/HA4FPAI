# ha-small本地复现

## 1. 本轮操作与产物

源码来自官方PyPI Home Assistant2026.10.4 sdist，原archive及元数据在`S:/work/WCIS/workenv/ha-small/`。本轮构建与原生语音依赖编译均发生在本机隔离Linux容器，没有使用板端编译。

一次性脚本依次为：

1. `workenv/ha-small/probe_upstream.py`：读取官方元数据。
2. `workenv/ha-small/acquire_source.py`：下载校验；Windows全量解包过慢后取消该步骤，保留下载原件，改由容器解包。
3. `workenv/ha-small/analyze_cut.py`：manifest/静态导入闭包与功能保留种子；补相对导入和基础实体域。
4. `workenv/ha-small/apply_cut.py`：对完整Linux源码副本删除无关集成，生成逐文件哈希与裁剪报告；当前脚本输出/ha-small-final，须在新环境运行，拒绝覆盖现有目标。
5. `workenv/ha-small/materialize_local.py`及`sync_final.py`：验证tar成员无链接/路径逃逸，提取裁剪树，核对保留文件哈希。脚本针对本轮已知路径，重跑前先读守卫，勿直接用于现有用户目录。
6. `workenv/ha-small/assemble_support.py`：复制维护用探针和示例、生成已测依赖锁与证据manifest。

中间归档包和未裁完整源码仍保留在workenv/隔离容器中，不夹进交付源码，也不清理其他会话文件。

## 2. 运行前提

- Linux x86_64、Python>=3.14.2；实测镜像为`docker.m.daocloud.io/library/python:3.14-slim`，当次解释器3.14.10。
- 独立venv；不要使用板上的系统Python3.8，不做版本检查绕过。
- 系统运行库：libturbojpeg0、ffmpeg；语音包没有对应轮子时需要g++/make/cmake/pkg-config/libspeexdsp-dev构建环境。
- 测试与生产配置分开。以下/runtime是容器专用测试目录，不是已有生产配置。

## 3. 隔离测试示例

在本机Podman运行；Windows路径按本机终端语法引用。以下以新建容器为例，勿与已存在的ha-small-dev重名。

```bash
podman run -d --name ha-small-verify --memory=1200m --cpus=2 \
  -p 127.0.0.1:18124:8123 \
  docker.m.daocloud.io/library/python:3.14-slim sleep infinity
podman cp ./ha-small ha-small-verify:/src
podman exec ha-small-verify sh -c \
  'apt-get update && apt-get install -y --no-install-recommends g++ make cmake pkg-config libspeexdsp-dev libturbojpeg0 ffmpeg'
podman exec ha-small-verify sh -c \
  'python -m venv /venv && /venv/bin/pip install /src && /venv/bin/pip install -r /src/cut/requirements-linux-x86_64.lock'
podman exec ha-small-verify sh -c \
  'mkdir -p /runtime && cp /src/examples/configuration.yaml /runtime/configuration.yaml && touch /runtime/HA_SMALL_TEST_ONLY'
podman exec -d ha-small-verify sh -c \
  'exec /venv/bin/hass -c /runtime > /tmp/ha-runtime.log 2>&1'
podman exec ha-small-verify /venv/bin/python /src/cut/functional_probe.py
```

探针会等待功能集加载，而非看到HTTP200就认定启动结束；真实onboarding创建随机测试账户，不输出密码/token；会调用隔离echo服务并切换测试实体。

## 4. 正常重启与持久化

仅针对上述专用容器，通过SIGTERM正常停HA并等待退出，再启动同一路径。不要kill现有用户进程：

```bash
podman exec ha-small-verify /venv/bin/python -c \
  'import psutil; p=[p for p in psutil.process_iter(["cmdline"]) if "/venv/bin/hass" in (p.info["cmdline"] or [])]; [x.terminate() for x in p]; gone,alive=psutil.wait_procs(p,timeout=30); assert not alive'
podman exec -d ha-small-verify sh -c \
  'exec /venv/bin/hass -c /runtime > /tmp/ha-restarted.log 2>&1'
podman exec ha-small-verify /venv/bin/python /src/cut/persistence_probe.py
```

该测试验证认证、文本helper、历史及SQLite完整性，不等于异常断电测试。

## 5. 来源与删除式审计

在源码根执行，不导入HA、不启动任何服务：

```bash
python cut/verify_source.py
```

预期输出：5909个上游文件、119个集成、modified_upstream_files=0、license_present=true。新增工具/说明不计入上游修改。任何必要产品补丁必须先登记PATCHES，并更新审计基线的“上游原件”和“修改件”关系，不可只改哈希掩盖变化。

## 6. 本轮证据与局限

原始结果在`workenv/ha-small/results/`：functional-results.json、persistence-results.json、runtime.log、install.log、environment-freeze.txt及manifest.json。30功能检查＋4正常重启检查全部通过；原始下载SHA在UPSTREAM中。

遇到并解决的问题：相对导入漏default_config；基础实体域翻译预载缺目录；语音依赖缺C++编译器；媒体库缺turbojpeg；测试把HTTP可用误当完整readiness；Assist测试实体未显式暴露。处理分别是恢复闭包、补系统依赖、等待实际功能就绪、按HA官方暴露接口授权，**均未修改HA源码**。

未执行：ARM64/板端部署、真实麦克风/扬声器/Wyoming设备、MQTT broker、真实摄像头、长期并发、完整浏览器UI及HA4FPAI实际业务联调。外部bridge示例只是模板，不能从本机echo测试推断生产业务已接通。

## 7. 2026-11-07 追加探针（G1/G2 本机可做部分）

在本机隔离容器（`HA_SMALL_TEST_ONLY` 标记的测试目录）内按顺序执行，均可重复：

```bash
python cut/mqtt_device_probe.py     # 配置流建 mqtt 条目 + 保留发现/状态/控制/broker 重启重订阅
python cut/camera_probe.py          # generic 合成 JPEG 源 -> 实体 -> /api/camera_proxy 快照
python cut/bridge_probe.py          # HA rest_command -> control /v1/commands，核对库中命令行
python cut/size_report.py           # 源码/安装/前端体积统计
python cut/arm64_inventory.py       # 117 个 pin 的 aarch64/cp314 轮子盘点
```

前置：容器内 mosquitto 以 `listener 1883 127.0.0.1` + `allow_anonymous true` 运行；合成图服务为
`python -m http.server 18095`（工作目录含 `cam.jpg`）；control 服务以
`python -m control.service --db /tmp/bridge/business.sqlite3 --host 127.0.0.1 --port 18100 --token <t>` 启动。
每个探针写 `/tmp/*-results.json`，副本已存 `cut/results/`。全部只证明协议/软件闭环，
真实硬件、板端部署与 ARM64 运行仍未验收。
