# 可复现源码包与构建验证

## 现状（2026-11-07）

| 项 | 值 |
|---|---|
| 源码包 | `ha-small-0.1.0-source.tar.gz`（由 `git archive` 从 `ha-small` 分支生成，含 `ha-small-0.1.0/` 前缀） |
| 包大小 / SHA256 | 6,135,211 B / `a3d48538ed5628230b64c09df32cf54e1da39b4e095f71438a15c9d85497be5f` |
| 包内审计 | `cut/verify_source.py` → 5909 上游文件、119 集成、修改 0、许可在 |
| 构建产物 | `homeassistant-2026.10.4-py3-none-any.whl`，8,131,064 B / `6176c2742673de8428508379781a36edeef7f62fb2e2dbc00176fe24eab9f836` |
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

## 边界

- 该包**不是** ARM64/deb 交付件，也未发布 Release；ARM64 有 11 个轮子在 cp314 下缺失（见 `cut/results/arm64-results.json`）。
- 包内 `cut/results/*` 是验证证据，不是运行时依赖。

> 本包由提交  生成；其后的提交仅改文档，不影响包内容。要与最新 HEAD 完全一致请按上节重新生成并自行记录哈希。
