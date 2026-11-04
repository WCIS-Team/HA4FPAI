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
