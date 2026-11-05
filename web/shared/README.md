# 控制台内嵌主题协议

`console-theme.js` 是 ggradio 与 Klar 本机代理共用的内嵌主题适配器。统一控制台在 `boards/console.py`；ggradio 从自己的 `/console-theme.js` 路由提供脚本，Klar 本机代理在显式内嵌的 HTML 中插入同源脚本标签。

## 行为

- 内嵌时隐藏 ggradio 的主题按钮、Klar 设置与向导中的外观切换组，由父控制台决定明暗。
- 根元素和 React 动态挂载的 `data-theme` 容器保持一致，不修改原应用的主题存储键或服务端配置。
- 单独打开直接返回，不安装监听、隐藏样式或修改主题；原主题按钮与偏好继续生效。
- 原本固定暗色且没有主题控件的直播／录像／人脸页面不在适配范围。

## URL 与消息

父控制台只给 iframe 地址追加：

| 参数 | 值 |
|---|---|
| `console_embed` | `1` |
| `console_theme` | `light` 或 `dark`，用于初始渲染 |
| `console_parent_origin` | 父控制台的 HTTP(S) origin，不含路径 |

独立打开的链接不带内嵌参数。子页面要求自己确实在 iframe 内，且父 origin 与存在时的 `document.referrer` 一致。父控制台使用 `referrerpolicy="origin"`。

子页就绪后发送 `{type: 'ha4fpai:theme-ready'}`。父页只接受来自当前 iframe 窗口及其 origin 的 ready；父页在 iframe load、ready 和主题切换时发送 `{type: 'ha4fpai:theme', theme: 'light'|'dark'}`，目标 origin 精确匹配 iframe 地址。子页只接受直接父窗口且 origin 匹配的主题消息。

Klar 外观组使用它发布前端的 `operatorChrome` 本地化标签识别；若上游改了控件结构或文案，应重新核实选择器，避免误隐藏其他设置。

## 验收与部署

1. 独立打开 ggradio 和 Klar，设置浅色，确认各自开关可见。
2. 在深色统一控制台内打开两页，确认子页为深色且主题控件隐藏。
3. 来回切换父页主题、刷新 iframe、切换应用标签，确认继续同步。
4. 通过「新窗口打开」进入原始地址，确认开关可见且先前浅色偏好仍在。
5. 来自子页自身的主题消息及非法主题值应被忽略；Klar 新挂载的主题容器须继续跟随父页。

ggradio 需同时部署 `web/ggradio/` 与 `web/shared/`，并重启原 Python 进程以启用新路由。Klar 本机代理需更新并重启；脚本移动后用 `--theme-bridge <console-theme.js 的绝对路径>` 指定适配器正本。这里的 JS 不含 token，不向页面注入任何鉴权信息。

2026-11-04 已用真实 ggradio HTTP handler、Klar 编译前端、Klar 本机代理和隔离 API 进行浏览器跨端口验证；生产服务与板端未变更。复跑入口保存在工作区 `workenv/console-embed-theme/preview.py`，测试端口为父页 10901、ggradio 7862、代理 10522、模拟 Klar 上游 10902。
