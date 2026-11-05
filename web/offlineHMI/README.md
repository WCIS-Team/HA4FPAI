# offlineHMI

板端 HDMI 直显的**原生 HMI**（不走浏览器）。方案与判据的正本：
[wiki/PS/WebFrontend/HDMI直显与网页最小化方案.md](../../../../wiki/PS/WebFrontend/HDMI直显与网页最小化方案.md)
（§九"原生 HMI 第四条路线"＋§一 显示链路约束）。

## 这是什么

在 1920×1080 RGB565 帧缓冲（udmabuf0，物理 `0x30100000`，显示读出地址寄存器 `0x40080054`）
上直接画界面，用 `/dev/input/event*` 读鼠标，操作走板上现成 REST（8091/8092/8093）。
**依赖极少**：板上已有 Python 3.8 / Pillow 9.3 / numpy 1.23（A 级取证），只需另装 1 个中文字体包。
目标：复现 8091（人脸识别）、8092（实时预览）、8093（录像）三张页面的功能，
**不含 klar**（10900/10520，按 2026-11-04 决定排除）。

## 目录

| 文件 | 复现对象 | 说明 |
|---|---|---|
| `hmi/` | —— | 共用层：显示（PNG 预演/udmabuf 上板双后端）、鼠标、REST 客户端、控件、**语音件**（`asr.py`：RMS 分段器/远程 ASR/录音源） |
| `app_preview.py` | 8092 实时预览 | 单帧 JPEG 轮询显示 + 拍照 |
| `app_recording.py` | 8093 录像 | 起/停、列表、回放（frame?at=）、下载、删除/恢复 |
| `app_photos.py` | 8091 人脸识别 | 相机识别（recognize-camera）、录入（enroll）、图库浏览 |
| `app_voice.py` | `lang/server/archive/webui.py` | FunASR 两段式语音识别的原生复现：整段识别 + 流式断句。**引擎不走已删的本地 funasr**，改打板上现役远程口（mimo_bridge :18090 `/api/v1/asr?llm=0`）；`--asr mock`＋`--source synth` 可完全离线跑 |
| **`kiosk.py`** | —— | **自助终端入口**（2026-11-04 她要求）：首页四宫格 → 点进某一页 → 右上角房子回首页；出不去，唯一出口是右下角连点 5 次 |
| `hmi/kiosk.py` | —— | kiosk 外壳：导航、右下角管理员热区、空闲归位 |
| `hmi/lockfile.py` | —— | 跨平台单实例锁（POSIX `flock`／Windows `msvcrt`）——两个进程同时写帧缓冲会互相覆盖 |

## kiosk 形态（她 2026-11-04 的要求）

「kiosk＝自助终端」在这块板上的**具体含义**（不是装什么东西，是运行方式）：

| # | 要求 | 怎么落实 |
|---|---|---|
| 1 | **开机就进、独占全屏** | 板上没有 X/WM/桌面，`kiosk.py` 直接独占 1920×1080 帧缓冲；启动交给 systemd（见下） |
| 2 | 用户**只能在界面里活动** | 只有首页四宫格 → 四个功能页；没有 shell、没有浏览器、没有别的进程能画屏 |
| 3 | **出不去** | 没有键盘退出路径；**唯一出口＝右下角不可见热区连点 5 次**（5 秒内），`--no-admin-exit` 可关死 |
| 4 | **空闲自动归位** | 无人操作 `--idle`（默认 120 s）自动回首页，像查询机/ATM |
| 5 | **崩溃自动拉起** | systemd `Restart=always`（见下）；进程内单实例锁防两实例互踩 |
| 6 | **退出复位** | SIGTERM/SIGINT 时把 `0x54` 写回复位默认 `0x07E00000`，不留脏地址（udmabuf 后端） |

手势与热区（1920×1080 坐标）：

```
右上角 房子 (1780,0,140,72)      单点 = 回首页
右下角 不可见 (1824,984,96,96)   5 秒内连点 5 次 = 管理员退出
```

自检（离线，不碰板）：`python selftest_kiosk.py`（脚本在 `workenv/hdmi-kiosk/`），
覆盖 T2 回首页／T3 五连点退出／T4 空闲归位／T5 单实例锁，2026-11-04 四项全 PASS。

### 板上开机自启（属 OS 层改动，登记 `os.md` 第七节）

```ini
# /etc/systemd/system/offlinehmi.service
[Unit]
Description=WCIS offline HMI (kiosk)
After=multi-user.target

[Service]
ExecStart=/usr/bin/python3 /root/offlineHMI/kiosk.py \
  --backend udmabuf --mouse evdev --host 127.0.0.1 \
  --asr http://127.0.0.1:18090 --source arecord:hw:0,0
Restart=always
RestartSec=2
StandardOutput=append:/var/log/offlinehmi.log

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now offlinehmi
```

rc.local 兜底（不想引入 unit 时）：`setsid python3 /root/offlineHMI/kiosk.py ... &`。
⚠️ 日志必须封顶（板上 `/tmp` 在根分区，历史上 `asr_status.jsonl` 2 MB/分钟爬满盘）——
用 `StandardOutput=append:` 到 `/var/log/` 并配 logrotate，或直接用 `journald` 的上限 drop-in。

## 开机门卫（hdmi_probe + 单个 systemd 单元）

正本：`deploy/offlinehmi.service`（**一个单元，`systemctl enable offlinehmi` 即全部**）。

**行为（对齐 2026-11-04 要求）**：

```
开机 → 门卫判定（默认 60 s）
  ├─ 判"有显示器" → exec 启动 kiosk（进程替换，单元继续跑 kiosk）
  └─ 判"没有"     → 以 0 退出（单元停在 inactive，**不再检测**，直到下次开机/手动）
```

**判定阶梯**（`hdmi_probe.py`）：

| 级 | 信号 | 现状 |
|---|---|---|
| L1 | DRM connector `status`（`/sys/class/drm/card*-*/status`） | 板子没有（无 DRM） |
| L2 | PS GPIO 里名字带 `hpd` 的线（`/sys/kernel/debug/gpio`） | 板子没有 |
| L3 | **一分钟人确认**：HDMI 上画倒计时，期间收到鼠标点击＝有人在看这块屏 | ✅ **今天唯一可用的** |
| L4 | `--assume yes|no` 强制结论 | 无屏调试用 |

**为什么 L3 不是真·HPD**（2026-11-04 板上实测，A）：引脚约束只有 4 个 TMDS 输出
（`AC17/AA15/Y16/W16`），**无 HPD、无 DDC**；`/sys/class/drm` 无 connector；`/dev/i2c-*` 不存在；
`/sys/kernel/debug/gpio` 4 个芯片只有 2 条线被占（PHY reset / SPI）；设备树、dmesg 搜
`hdmi|hpd|edid` 零命中 ⇒ **板子没有任何"显示器在不在"的回读信号**。等换了带 DRM 的位流、
或把 HPD 接进 PS GPIO，把 L1/L2 的 `--no-ui` 路径启用即变自动。

**部署（板上，属 OS 层改动，登记 `os.md` 第七节）**：

```bash
cp deploy/offlinehmi.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now offlinehmi      # enable=每次开机跑门卫；now=立即跑一次
# 手动只跑检测不启动：python3 hdmi_probe.py --no-ui    （纯检测，不画屏）
# 绕过门卫直接起 kiosk：systemctl start offlinehmi-kiosk  不存在——单元就一个，
#   不想等 60s 就 systemctl start offlinehmi && 手动点一下屏幕
```

⚠️ 两个注意：
1. **日志封顶**：`StandardOutput=append:/var/log/offlinehmi.log`，配 logrotate 或改用 journald
   （板上历史上 `asr_status.jsonl` 2 MB/min 爬满过盘，见 `wiki/PS` 磁盘清理 SOP）。
2. **判定依赖鼠标参与**：没有鼠标就没有"人确认"信号 ⇒ 无鼠标时永远判"没有"。
   这是 L3 的固有代价，不是 bug；真要自动检测只有 L1/L2 两条路。

## 深浅颜色主题

首页和预览、录像、人脸、语音四个功能页共用 `Theme`，内置 `dark`（原深色，默认）与 `light`（报告中的浅色配色）。五个入口均支持 `--theme dark|light`；顶部“浅色模式／深色模式”按钮即时切换，不重建页面或清空业务状态，回首页及再进入其他页也沿用当前主题。按钮文字表示点击后要切到的模式。

```bash
# 离线预演；不访问显示/音频设备或板端服务
python -B kiosk.py --theme light --backend png:S:/work/WCIS/workenv/hdmi-kiosk/light --mouse "mock:[]" --mock --frames 1 --no-single-instance
python -B app_voice.py --theme light --backend png:S:/work/WCIS/workenv/hdmi-kiosk/voice-light --mouse "mock:[]" --asr mock --source synth --frames 1
# 真 HDMI/鼠标验收就绪后的参数示例，本轮未在板上执行
sudo python3 kiosk.py --theme light --backend udmabuf --mouse evdev --host 127.0.0.1
```

运行时选择只保留在当前进程，重启按命令行值恢复。要固定浅色启动，在自己的 kiosk 启动命令（门卫单元则为 `--then-exec` 内的 kiosk 命令）加 `--theme light`；不会自动修改系统服务。颜色只影响界面，不改变照片/录像像素或语音后端；本机界面与回环服务无需外网，在线 ASR／云问答／Edge TTS 仍需网络。

2026-11-04 本机离线回归 `workenv/hdmi-kiosk/test_themes.py` 六项通过，覆盖运行中切换、四页共享/状态保留、鼠标点击分发、媒体不变、五入口深浅出图与非法值拒绝、Python 3.8 语法解析。图片及结果在 `workenv/hdmi-kiosk/theme-validation/`。未访问开发板，真 HDMI／USB 鼠标联合验收仍待做。

## 运行环境（按她 2026-11-04 定：不在板上跑，先校机/102 验证）

### 板上要装什么（一张清单，照着做）

**A. 已在板上，不用装**（2026-11-03 只读取证，A 级）：

| 项 | 读数 |
|---|---|
| `python3` | 3.8.10 |
| `numpy` | 1.23.0 |
| **Pillow** | **9.3.0**（`python3 -c "import PIL; print(PIL.__version__)"`）—— 界面绘制就靠它 |
| `arecord` | 1.2.2（alsa-utils）—— `app_voice.py --source arecord` 用它采麦克风 |
| `u_dma_buf` 内核模块 | `lsmod` 在；`/dev/udmabuf0` 存在，phys `0x30100000`、128 MiB |
| `/dev/mem` | root 可写（`crw------- root root`）—— sink 写 `0x40080054` 要它 |
| USB HID | 内核 `CONFIG_USB_HID=y`、dwc2 主机，插 USB 鼠标即出 `/dev/input/eventN` |

**B. 要装（1 个包）**：

```bash
apt-get install -y fonts-wqy-microhei   # 中文字体；板上 fc-list 只有 6 个字体，不装中文全是豆腐块
                                        # 各候选包尺寸对比见 repos/README.md §2.2
                                        # （microhei 5.1 MB ＜ zenhei 16.4 MB，选 microhei）
```

**C. 要插**：USB 鼠标（或带 HID 的键鼠）。设备号**现场查、别写死**（USB 重枚举会变，
板上 MCP01 的 event0/event1 就专门记过这条）：

```bash
cat /proc/bus/input/devices | grep -A3 -i mouse      # 看 Handlers 里的 eventN
# 或者直接让程序自己找：
python3 app_recording.py --backend udmabuf --mouse evdev ...
```

**D. 运行前提**：**root**（`/dev/mem` 与 evdev 权限）＋ **能收 1920×1080 的显示器**
＋ PL 位流带 HDMI 读出（现役 BOOT.bin 已是，见方案页 §一）。

> ⚠️ 两个容易踩的点（2026-11-04 核 RTL 得出）：
> ① **时序写死 1920×1080**（`color_bar.v` 的 `VIDEO_1920_1080`，由 `video_define.v:1` 选中；
> `video_rect_read_data` 的 `video_width/height` 也是 1920/1080 常数）——**改不了分辨率**，
> 只支持 1366×768 的老屏或不认 1080p 的屏会黑。
> ② **刷新率跟着像素时钟走**：`cam_clk = rx0_locked ? rx_usrclk : sys_clk_200m`（`sdi/rx_sdi.v:373`）。
> 接了 3G-SDI 1080p60 相机 → 148.5 MHz → 正好 **60 Hz**；**不接相机 → 回退 200 MHz → 同样
> 2200×1125 像素/帧 → 约 81 Hz**（wiki 里 VTC 那轮读到 80 fps，与此吻合）。
> 81 Hz 超出多数显示器 EDID 的标准档，**有的能锁、有的报 out of range**。
> ⇒ 不接相机直上 HMI 前，先拿你这台显示器实测一次彩条。

**E. 明确不需要**：Xorg、浏览器、Electron、gradio、ffplay、GStreamer、桌面环境。

### 三台验证机

- **校机（Win11-HV1）**：Python 3.15 已在 `D:\yzd\app\Python315\python.exe`，pip 可用。
  `pip install pillow numpy`（本轮已装：pillow 13.0.0 + numpy 2.5.4）。
  预演后端=PNG 落盘，无任何 Linux 依赖。
- **102（debian-h61）**：Debian 13，Python 3.13.5，`python3-pil python3-numpy`；可作第二验证台。
- **板上（最后一步才碰）**：只装 B 那一包。

### 命令速查

```bash
# 校机预演（无板）
python app_recording.py --backend png:out --mouse win32 --server http://<板IP>:8093
python app_voice.py    --backend png:out --mouse win32 --asr mock --source synth
# 板上真机
sudo python3 app_recording.py --backend udmabuf --mouse evdev --server http://127.0.0.1:8093
sudo python3 app_voice.py    --backend udmabuf --mouse evdev \
     --asr http://127.0.0.1:18090 --source arecord:hw:0,0
```

## 验证顺序

1. 校机：`python app_recording.py --backend png --server http://<板IP>:8093 --mock`（无板时用 `--mock` 内置假数据跑通 UI 逻辑）
2. 102/本机：连真实 8091/8092/8093 REST（板 IP 可达），PNG 预演确认布局与交互
3. 板上：先 `workenv/hdmi-kiosk/colorbar_fill.py --write` 彩条烟测（验位流带 HDMI 读出＋`0x54` 可写＋udmabuf 可用），过了再切 `--backend udmabuf`

## 不做什么

- 不装 Xorg / 浏览器 / Electron / 桌面环境
- 不做 klar（10900/10520）
- 不在本目录放运行产物（日志/截图落 `workenv/hdmi-kiosk/`）
