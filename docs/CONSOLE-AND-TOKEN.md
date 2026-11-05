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
| `/` | 一页看全：系统体检 + 命令面板 + 声纹库 + 执行历史 + 日志 + **内嵌页面标签** |
| `/api/health` | gateway/engine 单元状态与 RSS、三 Wyoming 端口、三板 API、内存 |
| `/api/commands` | 从 `klar_executor.DIRECT_COMMANDS` 自动生成按钮 |
| `/api/run?text=…&speak=1&dry=1` | 跑命令，可播报/演练，写执行历史 |
| `/api/voiceprint` | 声纹库路径/维度/人数/成员 |
| `/api/history` | 最近执行记录 |
| `/api/logs?name=gateway\|klar` | 服务日志尾部 |

**按类别内嵌页面**（每类独立 iframe，也可新窗口打开）：

| 顶部分类 | 页面 | 端口 | 内容 |
|---|---|---|---|
| 视频 | SDI 直播 | 8092 | H.264 实时画面 |
| 视频 | 录像管理 | 8093 | 开关录制、配额、回收站、录像列表 |
| 视频 | NPU 人脸 | 8091 | 人脸识别与 gallery（服务 503 时打不开） |
| 声音 | ggradio 语音试验台 | 浏览器所在电脑的 7861 | 参数配置、逐段运行、产物试听与下载；地址 `http://127.0.0.1:7861/` |
| Klar NLU 控制台 | Klar 原网页 | 本机代理 10521，转引擎 10520 | 语言包、自定义短语、播报模板、settings、对话历史、LLM |

“声音”还包含本控制台的“声纹库”子标签；“历史”独立显示执行记录。顶部切换分类时只显示对应内容，保留总览、命令和日志。首次进入类别才加载其内嵌页，切换到其他类别不会重新导航已加载的 iframe；视频子标签和声音子标签的选中状态分别保留。

### Klar 视觉风格与使用判据

统一控制台沿用 Klar 的炭灰／暖白主题与锈橙强调色（`#1c1c1c`、`#242424`、`#f4efe6`、`#c45c26`），顶部提供总览、命令、历史、视频、声音、Klar NLU 控制台、日志导航。标签采用橙色下划线，卡片、表单与状态色使用同一套主题变量。

右上角可切换明暗主题，选择保存在浏览器的 `ha4fpai-console-theme` 项，刷新后保留。字体优先使用本机 IBM Plex Sans／Mono，缺失时使用系统字体，不下载外部字体。桌面三列状态卡片，窄屏两列；日志与命令表单随宽度换行，历史表在自身容器内横向滚动。

验收：切换明暗并刷新，主题保持；320／390／768／1440 px 宽度下页面无横向溢出；键盘可聚焦导航和按钮，输入命令后回车与点击「执行」行为相同；ggradio 内嵌及新窗口链接保持可用。预览测试使用模拟 API，不执行设备命令。

### ggradio 内嵌的使用与判据

先在电脑上按 `web/ggradio/README.md` 启动试验台，再打开统一控制台，进入「声音」→「ggradio 语音试验台」。内嵌框显示参数面板与逐段试验台；切换标签时地址与高亮同步恢复。内嵌地址附带 `console_embed=1`、初始 `console_theme` 和 `console_parent_origin`；「新窗口打开」仍指向不带这些参数的 `http://127.0.0.1:7861/`。控制台不会自动启动 ggradio，也不会把它部署到板子。

### 内嵌页面统一明暗主题

内嵌的 ggradio 隐藏自身右下角主题按钮，Klar 原页隐藏外观切换组，只保留统一控制台右上角的开关。父页加载 iframe、收到子页 ready 消息或切换主题时，通过 `postMessage` 发送主题；子页同时校验父窗口与来源，刷新、切换标签和动态挂载组件后仍跟随父页。它不写子页原有的主题存储键，所以独立打开时保留自己的开关与偏好。

共用适配器正本为 `web/shared/console-theme.js`（协议及验收见该目录 README）。ggradio 服务新增同源 `/console-theme.js` 路由，部署时须一起带上 `web/shared/` 并重启 ggradio 进程。本机 Klar 代理 `workenv/klar-local-proxy/klar_local_proxy.py` 只给显式内嵌的 HTML 加入适配器；移动代理脚本后通过 `--theme-bridge` 指定脚本正本路径，不涉及 token 注入。旧进程需重启后才使用新路由与代理逻辑。

直播、人脸和录像的可见正本是固定暗色页，没有独立明暗按钮，本次不为它们新增主题功能。隔离测试使用真实 ggradio 页面、Klar 编译前端和本机代理，API 为模拟服务；已验证跨端口同步、刷新握手、控件隐藏、错误来源／主题值拒绝、独立打开恢复和偏好不被覆盖，未部署到板子。

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
