"""Qwen3-0.6B decode-only driver for the ZG330 board, ported from the vendor's
Qwen2-0.5B script (vendor copy on board: #196, /root/qwen2/qwen2_infer_pad3.py).

Ported facts (from Q3V1-decoder_ZG.json, read off the file, not assumed):
  1 Input op, 58 values: v_id0 input_ids fp32[1,1] @hostm,
                         v_id1 token_dynamic_fm fp32[1,3] @chunk(6@0),
                         v_id2..57 past kv fp16[1,8,512,128]
  1 Output op, 57 values: logits fp16[1,1,151936] then 56 present tensors
So: 28 layers x 2 = 56 kv tensors, 8 kv heads, head_dim 128 (Qwen2 was 24x2/2/64).

Two deliberate differences from the vendor script:
  - token_dynamic_fm carries 3 fp32 values (the ZG graph asks for 12 B; the vendor
    script feeds 8 B and the board rejects it, see ledger #196).
  - no ContentFilter: stop is EOS (151645/151643) or max_new_tokens only.

Run (board):  cd /root/qwen3 && printf '%s\n' '你是谁' '' | python3 q3_infer_board.py
(the trailing empty line is the exit channel -- stdin EOF otherwise floods tracebacks)
"""
import os
import sys
import time
import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
from tokenizers import Tokenizer

import icraft
from icraft import xir

sys.path.append("/root/qwen2/modelzoo_utils")
from pyrtutils.icraft_utils import openDevice, initSession
from icraft import xrt

Tensor = xrt.Tensor


class ModelConfig:
    config_path: str = "/root/qwen3/config/qwen3"
    decode_json: str = "/root/qwen3/jr_path/decode/Q3V1-decoder_ZG.json"
    decode_raw: str = "/root/qwen3/jr_path/decode/Q3V1-decoder_ZG.raw"

    max_decode_length: int = 512
    max_new_tokens: int = int(os.environ.get("Q3_MAX_NEW", "32"))
    dtype_kv: np.dtype = np.float16
    run_backend: str = "zg330"
    ip: str = "192.168.125.143"
    mmu_mode: bool = True
    cuda_mode: bool = False
    vocab_size: int = 151936
    stop_ids: Tuple[int, ...] = (151645, 151643)
    n_layers: int = 28
    n_kv_heads: int = 8
    head_dim: int = 128


def Tensor2Numpy(outputs: List[xrt.Tensor]) -> List[np.ndarray]:
    if not isinstance(outputs, list):
        outputs = [outputs]
    numpy_arrays = []
    for idx, item in enumerate(outputs):
        chunk = item.chunk()
        byte_size = chunk.byte_size
        offset = item.offset()
        tensor_shape = item.dtype().shape
        input_dtype = item.dtype().element_dtype
        s = str(input_dtype)
        if s == '"@fp(32)"':
            np_dtype = np.float32
        elif s == '"@fp(16)"':
            np_dtype = np.float16
        elif s == '"@uint(8)"':
            np_dtype = np.uint8
        elif s == '"@sint(16)"':
            np_dtype = np.int16
        else:
            raise RuntimeError("unhandled element dtype %s at output %d" % (s, idx))
        py_buffer = np.zeros(shape=tensor_shape, dtype=np_dtype)
        item.read(py_buffer, offset, byte_size)
        numpy_arrays.append(py_buffer)
    return numpy_arrays


class IcraftModel:
    def __init__(self, config: ModelConfig):
        self.config = config
        self.tokenizer = None
        self.device = None

    def load_model(self):
        # The board's tokenizers 0.13.3 cannot parse this tokenizer.json (raises
        # "data did not match any variant of untagged enum ModelWrapper" -- same failure as
        # 台账 #220 on ERNIE).  ids-in runs do not need it, so load on failure instead of
        # dying: text mode will then fail later with a clear message, not at apply time.
        try:
            self.tokenizer = Tokenizer.from_file(
                os.path.join(self.config.config_path, "tokenizer.json"))
        except Exception as e:
            self.tokenizer = None
            print("[warn] tokenizer load failed: %r" % e, flush=True)
        return self

    def init_device(self):
        if self.device is not None:
            return self
        self.device = openDevice(self.config.run_backend, self.config.ip,
                                 self.config.mmu_mode, self.config.cuda_mode)
        return self

    def _load_network(self, json_path: str, raw_path: str) -> xir.Network:
        network = icraft.xir.Network.CreateFromJsonFile(json_path)
        network.lazyLoadParamsFromFile(raw_path)
        return network

    def reset_device(self):
        if self.config.run_backend != "host" and self.device:
            self.device.reset(1)


class DecodeOnlySessionManager:
    def __init__(self, model: IcraftModel):
        self.model = model
        self.model.load_model().init_device()
        self.decode_network = None
        self.decode_session = None
        self.kv_dtype = None
        self._init_decode_network()

    def _init_decode_network(self):
        t0 = time.time()
        self.decode_network = self.model._load_network(self.model.config.decode_json,
                                                       self.model.config.decode_raw)
        print("[net] json+raw loaded in %.1f s" % (time.time() - t0), flush=True)
        t0 = time.time()
        self.decode_session = initSession(self.model.config.run_backend, self.decode_network,
                                          self.model.device, 1)
        print("[net] initSession %.1f s" % (time.time() - t0), flush=True)
        t0 = time.time()
        self.decode_session.apply()
        print("[net] apply %.1f s" % (time.time() - t0), flush=True)
        self.kv_dtype = self.decode_network.ops[0].outputs[2].tensorType()
        print("[net] ready, kv dtype=%s" % (self.kv_dtype,), flush=True)

    def _prepare_kv_cache(self, batch_size: int, seq_len: int) -> List[np.ndarray]:
        c = self.model.config
        return [np.zeros((batch_size, c.n_kv_heads, seq_len, c.head_dim), dtype=c.dtype_kv)
                for _ in range(c.n_layers * 2)]

    def _create_feed_dict(self, input_ids: np.ndarray, token_dynamic_fm: np.ndarray,
                          kv_cache: List[np.ndarray]) -> Dict[str, np.ndarray]:
        feed = {"input_ids": input_ids, "token_dynamic_fm": token_dynamic_fm}
        for i in range(self.model.config.n_layers):
            feed["past_key_values.%d.key" % i] = kv_cache[i * 2]
            feed["past_key_values.%d.value" % i] = kv_cache[i * 2 + 1]
        return feed

    def _numpy2Tensor(self, feed: Dict[str, np.ndarray]) -> List:
        out = []
        for idx, v in enumerate(feed.values()):
            if idx in (0, 1):
                out.append(Tensor(v.astype(np.float32), xir.Layout("*C")))
            else:
                out.append(Tensor(v.astype(np.float16), self.kv_dtype))
        return out

    def _forward(self, input_ids: np.ndarray, token_dynamic_fm: np.ndarray,
                 kv_cache: List[np.ndarray]):
        feed = self._create_feed_dict(input_ids, token_dynamic_fm, kv_cache)
        tensors = self._numpy2Tensor(feed)
        outputs = self.decode_session.forward(tensors)
        for t in outputs:
            t.waitForReady(datetime.timedelta(milliseconds=50000))
        self.model.reset_device()
        return Tensor2Numpy(outputs)

    def sample_logits(self, logits: np.ndarray) -> int:
        return int(np.argmax(logits))

    def run_prompt(self, prompt_tokens: List[int]) -> Tuple[int, List[np.ndarray]]:
        kv_cache = self._prepare_kv_cache(1, self.model.config.max_decode_length)
        for i, token in enumerate(prompt_tokens):
            tdf = np.array([[1, i + 1, 0]], dtype=np.float32)
            ids = np.array([[token]], dtype=np.int64)
            arrays = self._forward(ids, tdf, kv_cache)
            new_kv = arrays[1:]
            for j, nv in enumerate(new_kv):
                kv_cache[j][:, :, i, :] = nv[:, :, i + 1, :]
            if (i + 1) % 10 == 0:
                print("[prompt] %d/%d" % (i + 1, len(prompt_tokens)), flush=True)
        logits = arrays[0].reshape(1, 1, -1)[:, 0, :]
        return self.sample_logits(logits), kv_cache

    def run_decode(self, input_token: int, kv_cache: List[np.ndarray], seq_pos: int) -> Tuple[int, List[np.ndarray]]:
        tdf = np.array([[1, seq_pos, 0]], dtype=np.float32)
        ids = np.array([[input_token]], dtype=np.float32)
        arrays = self._forward(ids, tdf, kv_cache)
        logits = arrays[0].reshape(1, 1, -1)[:, 0, :]
        return self.sample_logits(logits), arrays[1:]


class Qwen3Generator:
    def __init__(self, config: Optional[ModelConfig] = None):
        self.config = config or ModelConfig()
        self.model = IcraftModel(self.config)
        self.session_mgr = DecodeOnlySessionManager(self.model)

    def generate(self, prompt: str, max_length: Optional[int] = None) -> str:
        max_length = max_length or self.config.max_new_tokens
        t_all = time.time()
        new_prompt = "<|im_start|>|user\n" + prompt + "<|im_end|>|\nassistant\n"
        prompt_tokens = self.model.tokenizer.encode(new_prompt).ids
        prompt_length = len(prompt_tokens)
        print("[gen] prompt tokens=%d" % prompt_length, flush=True)

        print("---STAGE: Prefill (decode net, one token per step)---", flush=True)
        t0 = time.time()
        next_token, kv_cache = self.session_mgr.run_prompt(prompt_tokens)
        print("[gen] prefill %.1f s, first token=%d" % (time.time() - t0, next_token), flush=True)

        generated = [next_token]
        decode_ms = []
        print("---STAGE: Decode---", flush=True)
        for step in range(1, max_length):
            if next_token in self.config.stop_ids:
                print("[gen] EOS at step %d" % step, flush=True)
                break
            t1 = time.time()
            next_token, kv_cache = self.session_mgr.run_decode(next_token, kv_cache, prompt_length + step - 1)
            decode_ms.append((time.time() - t1) * 1000)
            generated.append(next_token)
            if step % 10 == 0:
                print("[gen] %d/%d" % (step, max_length), flush=True)

        total_s = time.time() - t_all
        print("\n[生成完成，共生成 %d 个token]" % len(generated), flush=True)
        print("Total time: %.2f s" % total_s, flush=True)
        print("Average output speed: %.2f tokens/s" % (len(generated) / total_s), flush=True)
        if decode_ms:
            print("Average decode time: %.2f ms" % (sum(decode_ms) / len(decode_ms)), flush=True)
        return self.model.tokenizer.decode(generated)


def main():
    print("=== Qwen3-0.6B decode-only on ZG330 ===", flush=True)
    generator = Qwen3Generator()
    while True:
        try:
            prompt = input("\n请输入问题(回车退出): ").strip()
            if not prompt:
                break
            print("Generating...", flush=True)
            answer = generator.generate(prompt=prompt, max_length=generator.config.max_new_tokens)
            print("-" * 80)
            print("Answer:", answer, flush=True)
        except KeyboardInterrupt:
            print("\nUser interrupted", flush=True)
            break
        except Exception as e:
            print("Generation failed: %s" % e, flush=True)
            import traceback
            traceback.print_exc()
            print("-" * 80)
            continue


if __name__ == "__main__":
    main()
