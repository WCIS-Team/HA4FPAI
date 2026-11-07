# -*- coding: utf-8 -*-
"""HiFi-GAN v2 (PRelu fp16) 的 npuserv adapter —— 差异化配置全在本模型目录 board/config.json。

契约（adapter_lib）：
    def load(params) -> engine(.info / .generate(req, emit))

generate 请求（POST /generate 的 body 到 adapter 前已合并 registry defaults）：
    {"mel_b64": base64(fp32 [T,80])}        —— 直接给 mel
    {"frames": <int>}                        —— 无 mel 时生成确定性测试 mel（正弦扫频）
    {"to_file": "/path/x.wav"}               —— 结果写 WAV 并只返回路径与统计
    {"max_frames": <int>}                    —— 输出截断帧数（默认全部 600）

返回：{"wave_b64"|"wave_file", "samples", "rate", "sec", "peak", "scheme"}
零输出守卫：波形峰值==0 视为失效（vocos README 记录过第 2 句全零问题），触发即重建 session 重试一次。
"""
import base64
import datetime
import json
import os
import struct
import sys
import time
import wave

_HERE = os.path.dirname(os.path.abspath(__file__))
_cfg_path = os.path.join(_HERE, 'config.json')
_cfg = json.load(open(_cfg_path, encoding='utf-8'))
_G = _cfg['graph']
_ic39 = '/root/icraft39'
if os.path.isdir(_ic39) and _ic39 not in sys.path:
    sys.path.insert(0, _ic39)
# adapter_lib 住在 npuserv 目录（server 用 NPUSERV_HOME 注入）——两树布局都要能 import
_here = os.path.dirname(os.path.abspath(__file__))
for _p in (_here, os.environ.get('NPUSERV_HOME', '')):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np                                    # noqa: E402
import adapter_lib                                    # noqa: E402


class Engine(object):
    name = 'hifigan-v2'

    def __init__(self, cfg, np, icraft):
        self.cfg = cfg
        self.np = np
        self.icraft = icraft
        from icraft import xir
        from icraft import xrt
        self.xir, self.xrt = xir, xrt
        self.net = None
        self.session = None

    # ---- session 生命周期 -------------------------------------------------
    def _apply(self, device=None):
        import datetime
        net = self.icraft.xir.Network.CreateFromJsonFile(_G['json'])
        net.lazyLoadParamsFromFile(_G['raw'])
        # multi-resident：device 由 npuserv 注入（openDevice 每进程一次；
        # 第二次 openDevice 会破坏其它 session 的 DMA——2026-11-08 实测）
        sys.path.insert(0, '/root/qwen2/modelzoo_utils')
        from pyrtutils.icraft_utils import initSession
        if device is None:
            device = openDevice('zg330', '192.168.125.143', True, False)
        s = initSession('zg330', net, device, 1)
        t0 = time.time()
        s.apply()
        self.net, self.session = net, s
        return round(time.time() - t0, 1)

    def _drop(self):
        self.session = None
        self.net = None

    # ---- 前向 -------------------------------------------------------------
    def _forward(self, mel):                       # mel: 图声明的内存序 fp32
        Tensor = self.xrt.Tensor
        # 用网络自己声明的 TensorType（shape/dtype/layout 绑定图），不手写 Layout——
        # 手写 'NHWC' 曾让输入没进 device（图对残留缓冲推理，输出每次都不同）
        if getattr(self, 'intype', None) is None:
            self.intype = self.net.ops[0].outputs[0].tensorType()
        inp = Tensor(self.np.ascontiguousarray(mel, dtype=self.np.float32), self.intype)
        outs = self.session.forward([inp])
        for t in outs:
            t.waitForReady(datetime.timedelta(milliseconds=20000))
        arr = self.np.zeros((153600,), dtype=self.np.float32)
        outs[0].read(arr, outs[0].offset(), outs[0].chunk().byte_size)
        del outs
        return arr

    def _mel_from_req(self, req):
        if req.get('mel_b64'):
            raw = base64.b64decode(req['mel_b64'])
            mel = self.np.frombuffer(raw, dtype=self.np.float32).reshape(-1, 80)
            T = min(mel.shape[0], 600)
            mel = mel[:T]
        else:
            frames = int(req.get('frames', 120))
            T = min(max(1, frames), 600)
            # 确定性测试 mel：时间×频带正弦扫频，幅度 0~1
            t = (self.np.arange(T)[:, None] / max(1, T - 1))
            f = (self.np.arange(80)[None, :] / 79.0)
            mel = (0.5 + 0.5 * self.np.sin(6.2832 * (t * 3 + f * 2)) * (1 - f) ** 2).astype(self.np.float32)
        if T < 600:                                # 帧维重复 pad 到定长 600
            reps = int(self.np.ceil(600 / T))
            mel = self.np.tile(mel, (reps, 1))[:600]
        # ZG 图输入声明 [1,1,600,80] NHWC，但内存序对应逻辑 [80,600]（编译把
        # ONNX 的 [N,80,L] 转置融合了）——按实测：喂 [80,600] C 序波形才对齐
        return self.np.ascontiguousarray(mel.T)[None, None]

    def generate(self, req, emit):
        t0 = time.time()
        if self.session is None:
            self.apply_s = self._apply(device=getattr(self, 'shared_device', None))
        mel = self._mel_from_req(req)
        T = int(req.get('max_frames') or 600)
        T = min(max(1, T), 600)
        arr = self._forward(mel)
        wave_out = arr[:T * self.cfg['samples_per_frame']]
        peak = float(self.np.max(self.np.abs(wave_out))) if wave_out.size else 0.0
        if peak <= 1e-9 and self.cfg.get('runtime', {}).get('zero_output_guard'):
            emit({'zero_retry': True})
            self._drop(); self._apply(device=getattr(self, 'shared_device', None))
            arr = self._forward(mel)
            wave_out = arr[:T * self.cfg['samples_per_frame']]
            peak = float(self.np.max(self.np.abs(wave_out)))
            if peak <= 1e-9:
                raise RuntimeError('HiFi-GAN 输出全零（session 复用缺陷），重试无效')
        out = {'samples': int(wave_out.size), 'rate': self.cfg['sample_rate'],
               'sec': round(time.time() - t0, 3), 'peak': round(peak, 4),
               'wave_stats': {'min': round(float(self.np.min(wave_out)), 4),
                              'max': round(float(self.np.max(wave_out)), 4),
                              'mean': round(float(self.np.mean(wave_out)), 4),
                              'std': round(float(self.np.std(wave_out)), 4)},
               'scheme': 'hifigan-v2-prelu-fp16'}
        if req.get('to_file'):
            path = req['to_file']
            os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
            pcm16 = (self.np.clip(wave_out, -1, 1) * 32767).astype('<i2')
            with wave.open(path, 'wb') as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(self.cfg['sample_rate'])
                w.writeframes(pcm16.tobytes())
            out['wave_file'] = path
        else:
            out['wave_b64'] = base64.b64encode(
                self.np.clip(wave_out, -1, 1).astype(self.np.float32).tobytes()).decode('ascii')
        return out

    def close(self):
        self._drop()


def load(params, device=None):
    import icraft
    import numpy as np
    eng = Engine(_cfg, np, icraft)
    eng.shared_device = device
    eng.apply_s = eng._apply(device=device)
    return eng


if __name__ == '__main__':
    sys.exit(adapter_lib.run(load))
