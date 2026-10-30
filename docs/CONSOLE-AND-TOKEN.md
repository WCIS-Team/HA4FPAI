---
tags: [SOP]
updated: 2026-10-31
---

# klar App 401 与统一控制台

## 一、「Error: 401 Unauthorized / Set the same string as the token」是什么

**根因**：klar 引擎**没有配 token** 时，安全策略是「只允许 localhost 写入，
外部网络只读」。日志原文：

```
WARN klar_nlu::io::bootstrap: Kein Token: Schreiben nur von localhost;
                                Supervisor-Netz darf lesen
```

所以浏览器从**别的机器**打开 `http://板:10520` 的 Klar App，任何**写操作**
（改设置、存短语）都会被拒，弹出这个"请设置 token"的提示页。

**这不是 bug，是安全设计。** 但对我们（单机内网、无 HA 集成）来说必须配 token，
否则 App 里什么都存不了。

## 二、修法（已在本板落地）

```bash
# 1. 生成 token（32 hex）
python3 -c 'import secrets; print(secrets.token_hex(16))' \
  > /mnt/data/klar/.klar-token
chmod 600 /mnt/data/klar/.klar-token

# 2. 让 klar 读它
#    systemd unit 的 ExecStart 加 --token-file
--token-file /mnt/data/klar/.klar-token
```

**token 值本身不要写进 unit 文件**（unit 是明文、易被 git 带走），只写路径。

重启后日志里 **`Kein Token` 警告消失**，即为生效判据：

```bash
systemctl restart klar-nlu-engine
grep -i 'Kein Token' /mnt/data/klar/klar.log | tail -2
#   应该看不到新的一条
```

## 三、KLAR_UI_DIR —— 顺带修好的另一个坑

如果你打 `http://板:10520` 看到的是**德语占位页**：

```
Klar UI nicht gebaut
Bitte im web-Verzeichnis npm run build ausführen.
```

**不用真去 `npm run build`。** 根因是 klar 用 `tower-http serve_dir`，
默认找 `/usr/share/klar/ui`，而官方 aarch64 tarball 把 UI 放在 `./ui` 下。
二进制里的字符串证据：

```
KLAR_UI_DIR /usr/share/klar/ui
```

修法：

```ini
Environment=KLAR_UI_DIR=/mnt/data/klar/engine/ui
```

## 四、统一控制台（端口 10900）

`boards/console.py`，标准库 `http.server`，**不引新依赖**。

| 路由 | 作用 |
|---|---|
| `/` | 一页看全：系统体检 + 命令面板 + 声纹库 + 执行历史 + 日志 + **内嵌三个前端** |
| `/api/health` | gateway/engine 单元状态与 RSS、三 Wyoming 端口、三板 API、内存 |
| `/api/commands` | 从 `klar_executor.DIRECT_COMMANDS` 自动生成按钮 |
| `/api/run?text=…&speak=1&dry=1` | 跑命令，可播报/演练，写执行历史 |
| `/api/voiceprint` | 声纹库路径/维度/人数/成员 |
| `/api/history` | 最近执行记录 |
| `/api/logs?name=gateway\|klar` | 服务日志尾部 |

**内嵌页面**（iframe + 标签切换，也可新窗口打开）：

| 标签 | 端口 | 内容 |
|---|---|---|
| Klar NLU 控制台 | 10520 | 语言包、自定义短语、播报模板、settings、对话历史、LLM |
| SDI 直播 | 8092 | H.264 实时画面 |
| 录像管理 | 8093 | 开关录制、配额、回收站、录像列表 |
| NPU 人脸 | 8091 | 人脸识别与 gallery（服务 503 时打不开）|

### 判据

```bash
systemctl is-active klar-console                 # active
curl -s -o /dev/null -w '%{http_code}' \
  http://192.168.1.113:10900/                    # 200
curl -s -o /dev/null -w '%{http_code}' \
  http://192.168.1.113:10520/assets/index-*.js   # 200（资源能取到 iframe 才可用）
```

## 五、踩坑记录

| 现象 | 原因 | 修法 |
|---|---|---|
| Klar App 弹 401 | 无 token，外部写入被拒 | `--token-file` |
| 10520 显示「UI nicht gebaut」 | `KLAR_UI_DIR` 默认路径不匹配 tarball | 显式设 `KLAR_UI_DIR` |
| 10520 从 curl 只拿到 421 字节 | **正常**——那是 SPA 入口，页面由 JS 渲染 | 不用管 |
| 8091 打不开 | NPU 服务 503（摄像头源不可用） | 等源恢复 |

## 相关页面

- `docs/ARCHITECTURE-OVERVIEW.md` — 整体链路与 HA 逻辑对照
- `docs/ROUTE2-IMPLEMENTED.md` — 执行器与板 API 实测
