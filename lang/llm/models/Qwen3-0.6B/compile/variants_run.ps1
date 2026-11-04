$ErrorActionPreference='Continue'
$env:OMP_NUM_THREADS='8'
$py='D:\yzd\WCIS\MOD\qwen3\venv\Scripts\python.exe'
$seq=(Get-Content 'D:\yzd\WCIS\MOD\qwen3\seq11.txt' -Raw).Split(@(' ',',',"`t","`r","`n"),[System.StringSplitOptions]::RemoveEmptyEntries) -join ','
'PROMPT=' + $seq
foreach ($comb in @(@(1,1), @(0,0), @(0,1), @(1,0))) {
    $p = $comb[0]; $b = $comb[1]
    $j = 'D:\yzd\WCIS\MOD\qwen3\var_p' + $p + '_b' + $b + '.json'
    & $py 'D:\yzd\WCIS\MOD\qwen3\ref_hf.py' --model 'D:\yzd\WCIS\MOD\qwen3\Qwen3-0.6B' --simulate $seq `
        --steps 32 --pad-slots $p --pos-base $b --out $j *>&1 | Out-Null
    'DONE pad=' + $p + ' base=' + $b + ' rc=' + $LASTEXITCODE + ' -> ' + $j
}
