# HA 降级到旧版 Python：源码级可行性（2026-10-30 实测）

> 结论先行：**「改造成 py3.8 可运行」这件事，语法上要改 1326 个文件 / 1489 处，依赖上还有 14 个包没有 cp38 轮子，且语音必需件（`wyoming`/`assist_satellite`）在 py3.8 版 HA 里根本不存在。
> **这条路不可行**，不是工程量问题，是能力缺失问题。**

## 一、版本门槛的完整历史（PyPI 实测）

| 起版 | requires_python |
|---|---|
| 2021.2.0 | >=3.8.0 |
| 2022.2.0 | >=3.9.0 |
| 2023.2.0 | >=3.10.0 |
| 2023.8.0 | >=3.11.0 |
| 2024.4.0 | >=3.12.0 |
| 2025.2.0 | >=3.13.0 |
| 2026.3.0 | **>=3.14.2** |

**最后一个能跑 py3.8 的是 `2021.2.3`**（wheel 10.8 MB，解压 **27.2 MB**，973 个集成）。

## 二、版本检查在源码里只是几行

`homeassistant/const.py:29`
```python
REQUIRED_PYTHON_VER: Final[tuple[int, int, int]] = (3, 14, 2)
```

`homeassistant/__main__.py:28`
```python
if sys.version_info[:3] < REQUIRED_PYTHON_VER:
    print(f"Home Assistant requires at least Python {...}", file=sys.stderr)
    sys.exit(1)
```

**这一段改掉就能绕过**——它不是拦路虎。真拦路的是语法和依赖。

## 三、语法层：板上真实 py3.8 逐文件编译的结果

用 `py_compile` 对 **10279 个 .py** 逐个编译（板子自带的真 py3.8.10）：

| 结果 | 数量 |
|---|---|
| 通过 | 8665 |
| **失败（语法错误）** | **1614（15.7%）** |

失败的**不只是边角集成**，核心文件全在里面：

```
/config_entries.py     /backup_restore.py     /loader.py
/core.py               /core_config.py        /config.py
```

具体报错：

```python
# homeassistant/core.py:119
type CALLBACK_TYPE = Callable[[], None]
     ^ SyntaxError: invalid syntax

# homeassistant/loader.py:1693
def bind_hass[_CallableT: Callable[..., Any]](func: _CallableT) -> _CallableT:
              ^ SyntaxError: invalid syntax
```

这两处是 **PEP 695**（Python **3.12+** 的新类型参数语法）。

### 全量语法普查（正则统计，1326 个文件命中）

| 构造 | 出现次数 | 最低 Python |
|---|---|---|
| `type X = ...` 类型别名（PEP 695） | **1087** | 3.12 |
| `def f[T](...)` 泛型函数（PEP 695） | **293** | 3.12 |
| `class C[T]` 泛型类（PEP 695） | **266** | 3.12 |
| `X \| Y` 联合类型编写 | 65 | 3.10 |
| `match/case` | 52 | 3.10 |
| `dict \|=` 合并运算 | 46 | 3.9 |
| `isinstance(x, A \| B)` | 30 | 3.10 |

**HA 核心（非集成）就有 65 个文件命中**，重灾区：

```
/core.py              type=3  def=24 class=1
/helpers/typing.py    type=10 union=4
/helpers/dispatcher.py type=1 def=12
/util/logging.py               def=9
/helpers/deprecation.py        def=4 class=4
```

## 四、依赖层：14 个包没有 cp38 轮子

HA 2026.10.4 有 **48 个直接依赖**（固定版本）。逐个查 PyPI 是否有 cp38 + aarch64 轮子：

| 包 | 要求版本 | 现有轮子最低 |
|---|---|---|
| `cryptography` | 50.0.1 | **cp311-abi3** |
| `Pillow` | 12.3.0 | **cp310** |
| `orjson` | 3.11.9 | **cp310** |
| `zeroconf` | 0.151.5 | **cp310** |
| `lru-dict` | 1.4.1 | **cp310** |
| `fnv-hash-fast` | 2.0.3 | **cp311** |
| `ulid-transform` | 2.2.9 | **cp311** |
| `annotatedyaml` | 1.0.2 | **cp313** |
| `audioop-lts` | 0.2.2 | **cp313-abi3** |
| `uv` | 0.13.5 | 无 cp38 |

**要上板就得自己交叉编译这些 Rust/C 扩展的 arm64/py3.8 轮子**——而这些库自己不保证支持 py3.8 源码能编（`annotatedyaml` 直接标 cp313 起）。

## 五、最致命的一条：py3.8 时代的 HA 没有语音链

`2021.2.3`（唯一可 py3.8 的版本）的 973 个集成里，语音相关的只有：

```
cloud, conversation, intent, stt, tts
```

**没有 `wyoming`，没有 `assist_satellite`。**

而 Wyoming 集成是 HA **2023.5** 才进来的，Assist 卫星是 **2024.3** 前后才有。也就是说：

> **就算你把 2021.2.3 完整装到板上，它也接不了我们板上已经在跑的那条 Wyoming 语音链（10300/10301/10302）。**

这是**功能缺失**，不是改代码能补的（要补等于把 2023–2024 两年的语音架构自己重写一遍）。

## 六、唯一「现实」的折中，和它的代价

| 选项 | 需 Python | 结果 |
|---|---|---|
| A. 改 HA 2026.10.4 源码降到 py3.8 | 3.8 | ❌ 1326 文件 / 1489 处 PEP695 改造 + 14 个包自编轮子；改完是自维护分叉，每次升级重做 |
| B. 用 HA 2021.2.3（原生 py3.8） | 3.8 | ❌ 没有 Wyoming / assist_satellite，接不了现有语音链 |
| C. 板上装 py3.14 | 3.14 | ❌ focal 无包、无 deadsnakes、无 docker；只能上 `python-build-standalone` 静态版(~30 MB)，再带 238 MB HA + 依赖，**内存 993 MB 撑不住** |
| D. **拆分部署（现状）** | — | ✅ 板上跑语音链 + klar engine（374 MB），HA 在别的机器 |

## 七、我的判断

**HA 降级到 py3.8：不可行，建议不做。**

理由排序（从硬到软）：
1. **语音链缺失**（决定性）：py3.8 版 HA 没有 wyoming 集成，接了也白接。
2. **依赖无轮子**（硬）：14 个包含 Rust 扩展，需自编 arm64/py3.8。
3. **语法改造量大**（软但真实）：1489 处 PEP695，核心文件全中。
4. **无收益**：改完是个自维护分叉，HA 每次发版都要重做。

**如果你就是想让 HA 上这块板**，唯一有希望的路线不是降级，而是**装 py3.14 静态发行版 + 裁剪到 34 MB**（见 `HA-TRIM-PLAN.md`）。但那需要先把内存从 993 MB 的预算里挤出 300–400 MB，会挤掉现役 WCIS 链——**这需要你拍板，我不建议**。

## 相关页面

- 体积裁剪方案：`HA-TRIM-PLAN.md`
- 实测账：`MEASUREMENTS.md`
