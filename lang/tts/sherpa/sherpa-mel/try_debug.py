"""Try to enable sherpa's built-in debug output from Python.

Source says: gen_config.GetExtraInt("debug", config_.model.debug)
and when debug is on it prints "Sentence 0 tokens: [ni3(1117), hao3(...)]".
Try passing debug as a kwarg / via GenerationConfig.
"""
import os
import sherpa_onnx

BASE = r"D:\yzd\ttsbench\models"
D = os.path.join(BASE, "matcha-icefall-zh-baker")
TEXT = "你好，我是你的语音助手。"

tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
    model=sherpa_onnx.OfflineTtsModelConfig(
        matcha=sherpa_onnx.OfflineTtsMatchaModelConfig(
            acoustic_model=os.path.join(D, "model-steps-3.onnx"),
            vocoder=os.path.join(BASE, "vocos-22khz-univ.onnx"),
            tokens=os.path.join(D, "tokens.txt"),
            lexicon=os.path.join(D, "lexicon.txt"),
            dict_dir=os.path.join(D, "dict"),
            noise_scale=0.0),
        num_threads=1), max_num_sentences=1))

print("=== generate signature attempts ===")
import inspect
try:
    print(inspect.signature(tts.generate))
except Exception as e:
    print("sig:", e)
print("doc:", (tts.generate.__doc__ or "")[:600])

print("\n=== try debug=1 ===")
try:
    a = tts.generate(TEXT, sid=0, speed=1.0, debug=1)
    print("OK with debug=1, samples=%d" % len(a.samples))
except TypeError as e:
    print("TypeError:", str(e)[:200])
except Exception as e:
    print("%s: %s" % (type(e).__name__, str(e)[:200]))

print("\n=== try GenerationConfig if present ===")
print("module has GenerationConfig:", hasattr(sherpa_onnx, "GenerationConfig"))
if hasattr(sherpa_onnx, "GenerationConfig"):
    gc = sherpa_onnx.GenerationConfig()
    print("gc attrs:", [x for x in dir(gc) if not x.startswith("_")])
    try:
        gc.set_extra("debug", 1)
        print("set_extra ok")
    except Exception as e:
        print("set_extra failed:", str(e)[:120])
