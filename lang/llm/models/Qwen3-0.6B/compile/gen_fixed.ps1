param([int]$MaxNew = 24, [string]$Work = 'gen_fixed35', [int]$MaxSeconds = 2700,
      [string]$Json = 'jr_path/Q3V1/Q3V1-decoder_adapted.json',
      [string]$Raw = 'jr_path/Q3V1/Q3V1-decoder_adapted.raw')
$ErrorActionPreference = 'Continue'
$root = 'S:\apps\llm\agentwork\qwen3\compile'
$csv  = Join-Path $root ($Work + '_rss.csv')
$script:started = Get-Date
$PROMPT = '151644,872,198,105043,100165,11319,151645,198,151644,77091,198'

$env:PYTHONIOENCODING = 'utf-8'
Set-Location $root
$chain = Start-Process -FilePath 'python' `
  -ArgumentList '../host_gen.py','--json',$Json,'--raw',$Raw,'--work',$Work,'--prompt',$PROMPT,'--max-new',$MaxNew,'--pos-offset','1','--dump-format','SFB' `
  -WorkingDirectory $root -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $root ($Work + '.out.log')) `
  -RedirectStandardError  (Join-Path $root ($Work + '.err.log')) `
  -PassThru
'CHAIN_PID=' + $chain.Id | Write-Output
if (Test-Path $csv) { Remove-Item $csv }
@('t_s', 'name', 'pid', 'ws_mb', 'peak_ws_mb', 'priv_mb', 'peak_priv_mb') -join ',' | Out-File $csv -Encoding ascii

function Log-Procs($pfx) {
    $mine = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$pfx" -ErrorAction SilentlyContinue)
    $py = Get-Process -Id $pfx -ErrorAction SilentlyContinue
    $set = @()
    if ($py) { $set += $py }
    foreach ($m in $mine) {
        $g = Get-Process -Id $m.ProcessId -ErrorAction SilentlyContinue
        if ($g) { $set += $g }
    }
    $set += (Get-Process -Name icraft-run -ErrorAction SilentlyContinue)
    $t = [int]((Get-Date) - $script:started).TotalSeconds
    foreach ($p in $set | Sort-Object Id -Unique) {
        @( $t, $p.ProcessName, $p.Id,
           [int]($p.WorkingSet64 / 1MB), [int]($p.PeakWorkingSet64 / 1MB),
           [int]($p.PrivateMemorySize64 / 1MB), [int]($p.PeakPrivateMemorySize64 / 1MB) ) -join ',' |
            Out-File $csv -Append -Encoding ascii
    }
}

$alive = $true
while ($true) {
    $chain.Refresh()
    if ($chain.HasExited) { $alive = $false }
    Log-Procs $chain.Id
    Start-Sleep -Milliseconds 1000
    $elapsed = ((Get-Date) - $script:started).TotalSeconds
    if (-not $alive) { break }
    if ($elapsed -gt $MaxSeconds) { break }
}

'chain exited=' + (-not $alive) | Write-Output
$orphans = Get-Process -Name icraft-run -ErrorAction SilentlyContinue
if ($orphans) { 'killing leftover icraft-run: ' + ($orphans.Id -join ',') | Write-Output; $orphans | Stop-Process -Force }
$rows = Import-Csv $csv
$rows | Group-Object name | ForEach-Object {
    'PEAK name={0} peakWS={1} MB peakPrivate={2} MB samples={3}' -f `
        $_.Name,
        ($_.Group | Measure-Object -Maximum -Property peak_ws_mb).Maximum,
        ($_.Group | Measure-Object -Maximum -Property peak_priv_mb).Maximum,
        $_.Count
}
'CSV=' + $csv | Write-Output
