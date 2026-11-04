$env:OMP_NUM_THREADS = '4'
$py = 'D:\yzd\WCIS\MOD\qwen3\venv\Scripts\python.exe'
$P = '151644,872,198,105043,100165,11319,151645,198,151644,77091,198'
Set-Location D:\yzd\WCIS\MOD\qwen3
'--- canonical full-recompute greedy'
& $py ref_hf.py --model D:\yzd\WCIS\MOD\qwen3\Qwen3-0.6B --chain $P --steps 24 --show-text --out D:\yzd\WCIS\MOD\qwen3\fix_chain.json
'--- vendored convention (1 pad slot, 1-based positions), corrected loop'
& $py ref_hf.py --model D:\yzd\WCIS\MOD\qwen3\Qwen3-0.6B --simulate $P --steps 24 --pad-slots 1 --pos-base 1 --show-text --out D:\yzd\WCIS\MOD\qwen3\fix_p1b1.json
'--- ordinary HF cache (0 pad slots, 0-based), corrected loop'
& $py ref_hf.py --model D:\yzd\WCIS\MOD\qwen3\Qwen3-0.6B --simulate $P --steps 24 --pad-slots 0 --pos-base 0 --show-text --out D:\yzd\WCIS\MOD\qwen3\fix_p0b0.json
'DONE'
