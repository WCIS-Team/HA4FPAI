# klar 401 彻底消除（token 由你自己的电脑持有）

## 一、401 的真正来源

klar 的安全策略：**没有 token 时只允许 localhost 写入。**

```
WARN klar_nlu::io::bootstrap: Kein Token: Schreiben nur von localhost;
                                Supervisor-Netz darf lesen
```

浏览器从别的机器访问 `http://板:10520` 时，所有写操作（改设置、存短语）都 401。

### 源码级证据（前端 `web/src/` 公开，MIT）

```js
// web/src/api.ts —— 读 token 并塞进请求头
let e = localStorage.getItem(`klar_token`) || ``;
return { "content-type": `application/json`, ...e ? {"x-klar-token": e} : {} }
```

```js
// 设置 token 的唯一入口
function et(e){ e.trim() && localStorage.setItem(`klar_token`, e.trim()) }
```

⇒ UI 只从浏览器 localStorage 的 `klar_token` 读值。

### 服务端接受的两种头（实测，从外部网络打板子）

| 请求头 | 结果 |
|---|---|
| 无 header | **401** |
| `x-klar-token: <token>` | **200** ✅ |
| `Authorization: Bearer <token>` | **200** ✅ |

## 二、为什么不能"直接关掉校验"

二进制里**没有关闭鉴权的开关**。全部 `KLAR_*` 环境变量：

```
KLAR_ACT  KLAR_LANG_DIR  KLAR_LLM_API_KEY  KLAR_LLM_ENABLE_THINKING
KLAR_LLM_MODEL  KLAR_LLM_BASE_URL  KLAR_PARSE  KLAR_SUPPORT_BUNDLE
KLAR_TOKEN  KLAR_UI_DIR  KLAR_UI_LOCALE
```

`KLAR_TOKEN` 是**设 token**，不是**关校验**。而且它是 stripped 静态二进制，改不了。

## 三、⚠️ 已废弃的错误方案（留作教训）

**第一版**：在**板子上**跑 10521 代理，把 token **明文注入 HTML**：

```html
<script>localStorage.setItem('klar_token', "336b...")</script>
```

**这是漏洞**：任何能访问板子 10521 的人 `curl` 一下就能拿到 token。
已彻底删除（服务、脚本、日志全部移除，板子 10521 端口已释放）。

## 四、现在的正确做法：token 只在你电脑上

```
浏览器 → http://127.0.0.1:10521/  ← 你电脑上的本地代理
              ↓ 自动补 x-klar-token
          http://192.168.1.113:10520  ← 板子上的 klar
```

**职责分离：**

| 位置 | 持有什么 | 是否对外暴露 |
|---|---|---|
| 板子 `/mnt/data/klar/.klar-token` | 服务端 secret（校验用） | ❌ 不暴露，只在磁盘上 |
| **你电脑 `%USERPROFILE%\.klar-token`** | 同一份 token 的副本 | ❌ 只在本地文件 |
| 你电脑 `127.0.0.1:10521` 代理 | 运行时读 token 加头 | ❌ **只监听 127.0.0.1**，外网连不到 |

**关键：代理不再往页面注入任何东西。** 前端读不到 localStorage 就不发 `x-klar-token`，
但代理会**无条件补上**。所以：

- 页面 HTML 里**没有明文 token**（已验证 0 次出现）
- 不需要手填 token
- token 不出这台电脑

## 五、用法

### 一次性配置

把板子的 token 抄到本机（只需一次）：

```powershell
plink -ssh -batch -pw fmsh root@192.168.1.113 "cat /mnt/data/klar/.klar-token" > "$env:USERPROFILE\.klar-token"
```

### 每次使用

双击 `repos/HA4FPAI/tools/klar-local-proxy.cmd`，窗口别关。

浏览器开 **http://127.0.0.1:10521/**

### 实测判据

```powershell
# 通过本地代理：写操作应 200
curl -X POST -H "Content-Type: application/json" -d '{"personality":"default",...}' `
     http://127.0.0.1:10521/api/settings
#   -> 200   ✅

# 对照：直连板子仍 401（证明是代理的功劳）
curl -X POST -H "Content-Type: application/json" -d '{...}' `
     http://192.168.1.113:10520/api/settings
#   -> 401

# 页面里不该有明文 token
curl -s http://127.0.0.1:10521/ | Select-String "localStorage.setItem"
#   -> 无输出
```

**浏览器实测（Playwright）**：
```
url            = http://127.0.0.1:10521/#/
title          = Klar!
hasTokenPrompt = false        ← 401 提示消失
rootChildren   = 3            ← SPA 正常渲染
控制台错误      = 仅 favicon.ico 404（无害）
```

## 六、地址一览

| 地址 | 用途 | 前提 |
|---|---|---|
| **http://127.0.0.1:10521/** | **Klar UI（推荐，免 401）** | 本机代理要开着 |
| http://192.168.1.113:10520/ | 直连 klar（读可以，写 401） | 无 |
| http://192.168.1.113:10900/ | 统一控制台（内嵌标签已指 10521） | 本机代理要开着才有 Klar 页 |

## 七、安全对比

| | 旧方案（板子代理） | 新方案（本机代理） |
|---|---|---|
| token 存哪 | 板子，且注入 HTML | **只在本机文件** |
| 谁能拿到 token | **任何能访问 10521 的人** | 只有能登你电脑的人 |
| 监听范围 | `0.0.0.0:10521`（全网可达） | **`127.0.0.1:10521`（仅本机）** |
| 明文出现在页面 | **是** | **否** |
| 需要手填 | 否 | 否 |

## 八、文件

| 文件 | 作用 |
|---|---|
| `tools/klar_local_proxy.py` | 本地代理本体（只监听 127.0.0.1） |
| `tools/klar-local-proxy.cmd` | 双击启动脚本 |
| `%USERPROFILE%\.klar-token` | 本机 token（**已 gitignore**） |
| `/mnt/data/klar/.klar-token` | 板子服务端 secret（chmod 600，不入库） |

## 相关页面

- `docs/CONSOLE-AND-TOKEN.md` — 控制台与 KLAR_UI_DIR
- `docs/ARCHITECTURE-OVERVIEW.md` — 整体链路
