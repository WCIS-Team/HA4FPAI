"""Print sherpa's real token sequence with OfflineTtsModelConfig(debug=True)."""
import os
import sherpa_onnx

BASE = r"D:\yzd\ttsbench\models"
D = os.path.join(BASE, "matcha-icefall-zh-baker")
TEXT = "你好，我是你的语音助手。"

mc = sherpa_onnx.OfflineTtsModelConfig(
    matcha=sherpa_onnx.OfflineTtsMatchaModelConfig(
        acoustic_model=os.path.join(D, "model-steps-3.onnx"),
        vocoder=os.path.join(BASE, "vocos-22khz-univ.onnx"),
        tokens=os.path.join(D, "tokens.txt"),
        lexicon=os.path.join(D, "lexicon.txt"),
        dict_dir=os.path.join(D, "dict"),
        noise_scale=0.0),
    num_threads=1,
    debug=True)
tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
    model=mc, max_num_sentences=1))

a = tts.generate(TEXT, sid=0, speed=1.0)
print("SAMPLES %d  %.3f s" % (len(a.samples), len(a.samples) / a.sample_rate))
