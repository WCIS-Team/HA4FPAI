# HA 裁剪方案（校机编译 → 小到能上板）

> 前提：**HA Core 在 238 MB / py3.14 之下，本板装不了**（见 `ssh/MEASUREMENTS.md` §3）。
> 本页讨论的是「把 HA 裁到能上板」这件事**在技术上怎么走、要丢掉什么**，以及**代价是否值得**。
> 结论先行：**体积能砍到 86%，但 Python 3.14 这一关在这个板上过不去，因此裁剪解决不了上板问题。**

## 一、裁什么（基于 wheel 逐文件实测）

HA 2026.10.4 解压后 238.0 MB，其中 `homeassistant/components/` 占 228.7 MB（96%）、1523 个集成。

**保留集（61 个集成，24.6 MB）**

| 类别 | 集成 |
|---|---|
| 语音链路核心 | `assist_satellite` `conversation` `stt` `tts` `intent` `media_source` |
| 设备域（要控制的东西） | `light` `switch` `scene` `script` `automation` `cover` `climate` `fan` `lock` `media_player` `vacuum` `humidifier` `number` `select` `button` `camera` `weather` `valve` `water_heater` `alarm_control_panel` |
| 状态与历史 | `sensor` `binary_sensor` `recorder` `history` `logbook` `system_log` |
| 人工输入 | `input_boolean` `input_number` `input_select` `input_text` `input_button` `timer` `counter` `person` `zone` `group` `todo` `calendar` `event` `update` `device_tracker` |
| 基础设施 | `http` `websocket_api` `frontend` `api` `auth` `onboarding` `mobile_app` `config` `diagnostics` `homeassistant` `homeassistant_heuristic` `template` |

**丢掉集（1462 个集成，约 204 MB）**，按类别举例：

| 丢掉的东西 | 体积贡献 | 理由 |
|---|---|---|
| 云 IoT 全家桶 `home_connect` `tuya` `smartthings` `miele` `lg_thinq` `teslemetry` `victron_gx` | ~14 MB | 你没有这些设备 |
| `zha` `zwave_js` `matter` `shelly` `knx` | ~11 MB | 无 Zigbee/Z-Wave/Matter 硬件 |
| `mqtt` | 4.47 MB | **例外建议：留着**，ESPHome/Tasmota 常用 |
| `template` | 3.54 MB | **例外建议：留着**，脚本化常用 |
| `telegram_bot` | 2.55 MB | 无用 |
| `unifiprotect` `reolink` | 3.17 MB | 无对应摄像头 |
| `roborock` `habitica` `husqvarna_automower` `bang_olufsen` `growatt_server` `enphase_envoy` | ~8 MB | 无关 |
| `demo` | 1.41 MB | 开发用，可丢 |
| 其余 ~1440 个杂项集成 | ~150 MB | 绝大多数你都用不到 |

**体积结果**
- 全量：238.0 MB
- 裁剪后（含 mqtt/template）：约 **33.9 MB**
- **降幅 86%**，文件数从 55159 → 约 4000

## 二、但裁剪解决不了真正的墙（必须说清）

| 障碍 | 实测 | 裁剪能否解决 |
|---|---|---|
| Python 版本 | HA 2026.10.4 `requires_python >= 3.14.2`；板上 **3.8.10**，focal 无更高版本、无 deadsnakes、无 docker/podman | ❌ **不能**。裁体积不减依赖 |
| 依赖体积 | HA 48 个直接依赖（`aiohttp 3.14.5` `cryptography` `numpy` `SQLAlchemy…`）在 **arm64/py3.14** 上要自行交叉编译 | ❌ 大大增加难度 |
| 内存 | 993 MB，四条语音服务已用 374 MB | ❌ 裁完 HA 仍要 200–400 MB |
| 许可/维护 | 裁完是自己维护的分叉，HA 每次升级都要重做 | —— 成本很高 |

**所以：能从 238 MB 砍到 34 MB，但上不了板是因为 Python 3.14 + arm64 依赖编译，不是因为体积。** 这个方向我建议**不做**。

## 三、如果坚持要做，最省事的路线（校机）

1. **不在板上编译，全部在校机**（EPYC 9654 / 125 GB / Debian 13，符合项目纪律「绝不在 113 编译」）。
2. 校机建 `py3.14`（`uv python install 3.14` 或源码编译）。
3. 用 `pip download --platform manylinux2014_aarch64 --only-binary :all:` 拉 arm64 wheel；无 wheel 的（`cryptography`、`numpy` 等）走 `crossenv` + `CROSS_COMPILE` 交叉编译。
4. 裁剪脚本（本仓库可提供）：解 wheel → 保留集白名单 → 重打包 → `zipapp` 或直接目录分发。
5. 传板后需 `py3.14` 运行时（**板上没有**）→ 还得用 `python-build-standalone` 的 aarch64 静态发行版（约 30 MB）。

**最小可行组合约 = 34 MB(HA) + 30 MB(py3.14) + 依赖若干 ≈ 80–120 MB**，仍吃 200 MB+ 内存。

## 四、我的建议

**不做裁剪版 HA 上板。** 保持现在的拆分：
- 板上：voice 四条服务 + klar engine（已验证，占 15 MB 盘 / 374 MB 内存）
- HA 本体：放有 py3.14 的机器（你的电脑/小主机/NAS），通过 Wyoming 网络连过来

如果你的目标是「单机全包」，更划算的是**换一台能跑 HA 的小机器**，而不是把 HA 切一半。
