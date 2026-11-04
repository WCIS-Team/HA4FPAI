# klar/ — klar 部分（NLU 集成 + 语言包 + 文档 + 部署件）

本目录是 HA4FPAI 仓里「klar 相关」的全部内容。语音链**不在本仓**
（已按决定迁到 `repos/HA4FPAI/lang/server` 实现，旧实现存档在
`repos/HA4FPAI/archiver/ha4fpai-voice/`）；本仓保留的是
「听懂（klar）→ 落到板子 API（executor）」这一层。

## 目录

| 路径 | 作用 |
|---|---|
| `klar_executor.py` | 双通道执行器：通道 B 直连命令表（~0 ms，确定性），通道 A 走 klar `10520` HTTP parse。把 intent+slots 映射到板子 API（8091/8092/8093）与 `door_unlock` |
| `make_registry.py` | 生成 klar 的 `core.entity_registry` + `core.area_registry`（本板自有实体命名，供通道 A） |
| `langpacks/zh_cn_speech.rs` | **当前生效（手工重译版）**：键名/顺序与原版完全一致，可直接编译；每条改动带 `// 原文` 注释 |
| `langpacks/zh_cn_speech.generated.rs` | 上游 zh-CN **原机翻版**留档（首行 `// @generated ... DO NOT EDIT.`，3098 B） |
| `langpacks/zh_cn_pack.rs`、`zh_cn_verbs.rs` | zh-CN 识别句式包、动词包（原样抽出） |
| `langpacks/en_speech.rs`、`en_pack.rs`、`en_verbs.rs` | 手写英文参考包（重译时的对照原文） |
| `langpacks/_verify.py` | 结构校验：键数/顺序/占位符/改动条数（机械验证，非目测） |
| `docs/KLAR-401-BYPASS.md` | klar 401 根因与本机 token 代理 |
| `docs/KLAR-CUSTOM-PHRASES.md` | 自定义短语被 `safety_decision` 判 unsafe 的完整证据链 |
| `deploy/klar-nlu-engine.service` | klar engine systemd 单元（10500 Wyoming intent + 10520 HTTP） |
| `deploy/deploy-klar-ui.sh` | 校机构建 klar 前端 dist 并部署上板（本机中转） |

## 语言包状态（重要）

- 重译版是「备好的补丁」，**尚未上板**：klar 外置包 `ExternalPack` 结构**没有 `speech` 字段**，
  改回复话术必须改源码重编 Rust；校机目前只有 Node、**无 Rust 工具链**。
- 校验基线（`_verify.py`）：67 键 / 键名与顺序一致 / 占位符 0 处不一致 / 话术条目改动 40–64 条。
- 详见 wiki：`wiki/SOP/Wyoming协议与klar接入.md` §3。

## 部署布局提醒

板上 python 文件**平铺在 `/mnt/data/klar/`**，所以本目录的分层只是仓库组织，不影响板上运行。
`console.py`/`door_unlock.py` 与 `klar_executor.py` 里已加了互指的 `sys.path` 兜底，
仓库（分层）与板上（平铺）两种布局都能正常 import。
