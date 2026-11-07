# 可复现源码包与构建验证

## 现状（2026-11-07）

| 项 | 值 |
|---|---|
| 源码包 | `ha-small-0.1.0-source.tar.gz`（由 `git archive` 从 `ha-small` 分支生成，含 `ha-small-0.1.0/` 前缀） |
| 包大小 / SHA256 | 6,850,596 B / `04f014cccc00635bb3d29e5cb24afb0ecf6b482716c99401784381ae1f12826e` |
| 包内审计 | `cut/verify_source.py` → 5909 上游文件、119 集成、修改 0、许可在 |
| 构建产物 | `homeassistant-2026.10.4-py3-none-any.whl`，**8,280,826 B** / `6bd60dc72c88312185fd5563c1d6f30ddd968b9aa0d4c1c57d4c638f1b0f5c2b`（2026-11-07 由 128 集成树重建；全新 venv 安装 + `pip check` + `check_config` 均通过，证据 `cut/results/wheel-validation.json`）
| 未做 | ARM64 包、deb、Release、任何远端推送 |

## 重新生成

```bash
git -C <本分支工作树> archive --format=tar.gz --prefix=ha-small-0.1.0/ -o ha-small-0.1.0-source.tar.gz HEAD
sha256sum ha-small-0.1.0-source.tar.gz
```

## 验证与构建

```bash
mkdir -p /tmp/pkg && tar -xzf ha-small-0.1.0-source.tar.gz -C /tmp/pkg --strip-components=1
python /tmp/pkg/cut/verify_source.py        # 期望：5909 文件 / 119 集成 / 修改 0 / license true
python -m venv /tmp/v && /tmp/v/bin/pip wheel --no-deps -w /tmp/whl /tmp/pkg
# 安装（Linux x86_64 实测环境）：
/tmp/v/bin/pip install /tmp/whl/homeassistant-2026.10.4-py3-none-any.whl
# 运行（独立测试目录，勿指向生产配置）：
mkdir -p /tmp/ha-lab && cp /tmp/pkg/examples/configuration.yaml /tmp/ha-lab/configuration.yaml
/tmp/v/bin/hass -c /tmp/ha-lab
```

构建与运行要求 Python >= 3.14.2；实测环境为 Linux x86_64 / Python 3.14.10。语音原生依赖
（pyspeex-noise、pymicro-vad）与媒体库（libturbojpeg、ffmpeg）需按 REPRODUCE 的说明准备。
本包只含源码，不含前端 wheel 之外的 Python 依赖，安装时由 pip 按 pyproject 解析。

## ARM64 依赖供给（2026-11-07 实测）

| 阶段 | 结论 |
|---|---|
| 轮子盘点（窄标签） | 106/117，11 个"缺" |
| 轮子盘点（真实标签组合） | **112/117**，5 个缺：pymicro_vad / pyspeex_noise / PyTurboJPEG / PyQRCode / PyRIC |
| 源码包交叉编译 | 2 个出 `linux_aarch64` 轮；3 个是纯 Python 轮 |

复现：

```bash
apt-get install -y gcc-aarch64-linux-gnu g++-aarch64-linux-gnu
export CC_aarch64_linux_gnu=aarch64-linux-gnu-gcc CXX_aarch64_linux_gnu=aarch64-linux-gnu-g++
export _PYTHON_HOST_PLATFORM=linux_aarch64 AR_aarch64_linux_gnu=aarch64-linux-gnu-ar
export LDSHARED="aarch64-linux-gnu-gcc -shared"
pip wheel --no-deps --no-binary :all: --no-cache-dir -w /tmp/a64-w3 pymicro_vad==1.0.1 pyspeex_noise==1.0.2
```

`--no-binary :all: --no-cache-dir` 是必需的：否则 pip 会拿缓存里的 x86 轮冒充（本轮实测踩到）。
**边界：只证明"依赖有 aarch64 产物"，没有在 ARM64 上安装或启动过。**

## 边界

- 该包**不是** ARM64/deb 交付件，也未发布 Release；ARM64 有 11 个轮子在 cp314 下缺失（见 `cut/results/arm64-results.json`）。
- 包内 `cut/results/*` 是验证证据，不是运行时依赖。

> 本包由提交 `83da433` 生成；其后的提交仅改文档，不影响包内容。要与最新 HEAD 完全一致请按上节重新生成并自行记录哈希。

## 裁剪验证方法（2026-11-07 教训）

**不要用 `pip install --force-reinstall` 验证裁剪结果。** pip 不会删除新版本中已不存在的文件，
旧集成的目录会留在安装树里，掩盖真实的 `ImportError`。本仓库第一版试删（129→119）就是这样
"通过"了 30/30 功能回归，却在 ARM64 干净安装时立刻崩在 `bootstrap.py` 的 `search` 预导入上。

正确姿势：

```bash
python -m venv /tmp/venv-fresh      # 全新 venv，不复用
/tmp/venv-fresh/bin/pip install /path/to/source
/tmp/venv-fresh/bin/hass --script check_config -c /tmp/cfg
```

另：静态导入扫描必须处理跨行括号导入（`from .components import (
  a as a1,
  b as b1,
)`），
按行匹配会漏。参考 `cut/next01_import_scan_v2.py`。

## 更正：伪造的 "aarch64" 轮子（2026-11-07）

用 `_PYTHON_HOST_PLATFORM=linux_aarch64` 配合宿主编译器产出的是**文件名伪装成 aarch64 的 x86_64 轮**：

```bash
# 之前（错误）：
export _PYTHON_HOST_PLATFORM=linux_aarch64
export LDSHARED="aarch64-linux-gnu-gcc -shared"   # 未真正生效
pip wheel --no-binary :all: pymicro_vad==1.0.1    # → *-linux_aarch64.whl（内含 x86_64 .so）
```

判据（必须在交付前跑）：

```bash
python - <<'PY'
import zipfile, glob
for w in glob.glob("*.whl"):
    with zipfile.ZipFile(w) as z:
        for n in z.namelist():
            if n.endswith(".so"):
                data = z.read(n)[:20]
                machine = int.from_bytes(data[18:20], "little")
                print(w, n, "EM_AARCH64(183)" if machine == 183 else "WRONG(%d)" % machine)
PY
```

**结论**：`pymicro_vad`、`pyspeex_noise` 仍缺真 aarch64 轮；`PyQRCode`/`PyRIC`/`PyTurboJPEG` 是纯 Python，没有问题。
`setuptools` 之类的构建依赖在 qemu 下取不到，故本轮未能在 ARM64 内重建。

## 裁剪验证规则（三轮教训，2026-11-07 定稿）

要断言"某集成可删"，必须同时满足三条，且必须用**干净 venv**验证：

1. **manifest 硬依赖**：没有任何**保留**集成的 `manifest.json` 的 `dependencies` 里出现它。
   （`after_dependencies` 是软依赖，允许缺失。）——第二轮就是漏了这条，`map_tiles` 被 `frontend` 硬依赖。
2. **静态导入**：没有任何模块 `from ...components.X` / `from .components import ( ... X ... )`。
   必须**先折叠跨行括号导入再匹配**，按行扫会漏（`bootstrap.py` 的 `search`）。
3. **bootstrap 预导入**：不在 `homeassistant/bootstrap.py` 的预导入列表中。

验证命令与判据：

```bash
python -m venv /tmp/v-fresh && /tmp/v-fresh/bin/pip install /path/to/src
/tmp/v-fresh/bin/hass --script check_config -c /tmp/cfg
# 判据：不只看 exit code，还要 grep 两套写法：
#   "ERROR"、"Invalid config"、"Integration error"、"not found"
```

`hass --script check_config` 在仅有集成缺失时**仍可能 exit=0**，错误在正文里（`Integration error: ...`）。
只看退出码或只 grep 大写 ERROR 会得到假通过。

## 产物重建命令（2026-11-07，128 集成树）

```bash
# 源码包（含 examples / cut / 文档，不含 .git）
git archive --format=tar.gz --prefix=ha-small-0.1.0/ -o ha-small-0.1.0-source.tar.gz HEAD

# wheel：需要一个带 setuptools 的环境；Python 3.14 的 venv 默认不带 setuptools
python -m venv /tmp/v && /tmp/v/bin/pip install setuptools wheel
/tmp/v/bin/pip wheel --no-deps --no-build-isolation -w /tmp/out .
```

注意：`--no-build-isolation` 配合缺 `setuptools` 会报
`BackendUnavailable: Cannot import 'setuptools.build_meta'`；而开隔离在离线/慢网下又会卡在
`Installing build dependencies`。所以先把纯 Python 的 `setuptools`+`wheel` 放进 venv 再关隔离。
