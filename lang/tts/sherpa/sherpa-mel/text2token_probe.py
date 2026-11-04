import sherpa_onnx
from sherpa_onnx.utils import text2token

TOK = r"D:\yzd\ttsbench\tokens_fixed.txt"
TEXTS = ["你好，我是你的语音助手。"]

for ttype in ("fpinyin", "ppinyin"):
    try:
        r = text2token(TEXTS, tokens=TOK, tokens_type=ttype, output_ids=False)
        print("=== %s ===" % ttype)
        print("  tokens:", r)
    except Exception as e:
        print("=== %s === ERROR %s: %s" % (ttype, type(e).__name__, str(e)[:120]))

print()
try:
    ids = text2token(TEXTS, tokens=TOK, tokens_type="fpinyin", output_ids=True)
    print("ids: ", ids)
    print("count:", [len(x) for x in ids])
except Exception as e:
    print("ids ERROR:", type(e).__name__, str(e)[:200])
