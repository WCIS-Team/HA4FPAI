import os, sys, time
os.environ.setdefault('MODELSCOPE_CACHE', r'D:\yzd\WCIS\MOD\qwen3\ms_cache')
from modelscope import snapshot_download

t0 = time.time()
dst = r'D:\yzd\WCIS\MOD\qwen3\Qwen3-0.6B'
p = snapshot_download('Qwen/Qwen3-0.6B', local_dir=dst, allow_patterns=[
    'config.json', 'generation_config.json', 'tokenizer_config.json', 'tokenizer.json',
    'vocab.json', 'merges.txt', 'special_tokens_map.json', 'chat_template.json',
    '*.safetensors', 'model.safetensors.index.json', 'README.md', 'LICENSE',
])
print('DOWNLOADED_TO', p)
tot = 0
for root, dirs, files in os.walk(dst):
    for f in files:
        fp = os.path.join(root, f)
        sz = os.path.getsize(fp)
        tot += sz
        print('FILE', os.path.relpath(fp, dst).replace('\\', '/'), sz)
print('TOTAL_GB', round(tot / 2**30, 3), 'SECS', round(time.time() - t0, 1))
