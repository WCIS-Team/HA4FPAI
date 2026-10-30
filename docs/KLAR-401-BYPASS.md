# klar 401 彻底消除（自动补 token 代理）

## 一、401 的真正来源

klar 的安全策略：**没有 token 时只允许 localhost 写入。**

```
WARN klar_nlu::io::bootstrap: Kein Token: Schreiben nur von localhost;
                                Supervisor-Netz darf lesen
```

浏览器从别的机器访问 `http://板:10520` 时，所有写操作（改设置、存短语）都 401。

### 源码级证据（前端 `ui/assets/index-Bg17n22J.js`）

```js
// 读 token 并塞进请求头
let e = localStorage.getItem(`klar_token`) || ``;
return { "content-type": `application/json`, ...e ? {"x-klar-token": e} : {} }

// 设置 token 的唯一入口
function et(e){ e.trim() && localStorage.setItem(`klar_token`, e.trim()) }
```

⇒ **UI 只从浏览器 localStorage 的 `klar_token` 读值。** 只要那个 key 有值，
401 就不再出现。

### 服务端接受的两种头（实测，从外部网络打板子）

| 请求头 | 结果 |
|---|---|
| 无 header | **401** |
| `x-klar-token: <token>` | **200** ✅ |
| `Authorization: Bearer <token>` | **200** ✅ |

## 二、为什么不能"直接关掉校验"

二进制里**没有关闭鉴权的开关**。我查过全部 `KLAR_*` 环境变量：

```
KLAR_ACT  KLAR_LANG_DIR  KLAR_LLM_API_KEY  KLAR_LLM_ENABLE_THINKING
KLAR_LLM_MODEL  KLAR_LLM_BASE_URL  KLAR_PARSE  KLAR_SUPPORT_BUNDLE
KLAR_TOKEN  KLAR_UI_DIR  KLAR_UI_LOCALE
```

其中 `KLAR_TOKEN` 是**设 token**，不是**关校验**。所以只剩两条路：

| 方案 | 可行性 |
|---|---|
| 改 klar 二进制 | ❌ 它是 stripped 静态二进制，改不了 |
| **同源代理自动补 header** | ✅ 本方案 |

## 三、方案：`klar_autotoken.py`（端口 10521）

一个极薄的同源代理：

1. 浏览器访问 **10521**（不是 10520）
2. 代理把请求转发到 klar 10520，**自动补 `x-klar-token`**
3. 对 HTML 响应**注入一小段 JS**，把 token 预置进 `localStorage`

```js
<script>(function(){try{localStorage.setItem('klar_token','<token>')}catch(e){}})();</script>
```

这样**两层都覆盖**：代理层的 fetch 自带 token，前端自己的 fetch 也从 localStorage 拿到。

### 实测判据

```bash
# 从外部机器（关键：不能用 localhost 测，localhost 本来就免 token）
curl -X POST -H 'Content-Type: application/json' -d '{"personality":"default",...}' \
     http://192.168.1.113:10521/api/settings
#   -> 200   ✅ 代理生效

curl -X POST -H 'Content-Type: application/json' -d '{...}' \
     http://192.168.1.113:10520/api/settings
#   -> 401   （直连仍 401，作对照，证明是代理的功劳）
```

实测结果：
```
10521（代理，无 header）    http=200   ✅
10520（直连，无 header）    http=401   （对照正常）
10521 页面含 localStorage.setItem('klar_token')  ✅
```

## 四、访问地址变了

| 地址 | 用途 |
|---|---|
| **http://板:10521/** | **Klar UI（免 401，推荐）** |
| http://板:10520/ | 直连 klar（写操作仍 401） |
| http://板:10900/ | 统一控制台（内嵌标签已改指 10521） |

## 五、安全说明（必须知道）

**这个代理等于把 klar 的写权限开放给任何能访问 10521 的人。**

- 家庭内网：可接受
- 要暴露公网：**必须自己在前面加鉴权**（例如 nginx basic auth）

它不是"绕过了安全"，而是"由代理持有凭据、自动出示"——
但这确实降低了访问门槛，等价于把 10521 当成内网可信入口。

## 六、文件与单元

| 文件 | 作用 |
|---|---|
| `boards/klar_autotoken.py` | 代理本体 |
| `deploy/klar-autotoken.service` | systemd 单元（端口 10521，上游 10520） |
| `/mnt/data/klar/.klar-token` | token 值（chmod 600，**不入库**）|

## 相关页面

- `docs/CONSOLE-AND-TOKEN.md` — 401 与 KLAR_UI_DIR 两个坑
- `docs/ARCHITECTURE-OVERVIEW.md` — 整体链路
