param([int]$WaitMs = 1500)
$ErrorActionPreference = 'Continue'
try {
    $p = New-Object System.IO.Ports.SerialPort 'COM6',115200,'None',8,'One'
    $p.NewLine = "`r`n"
    $p.Open()
    $p.DiscardInBuffer()
    $p.Write("`r`n")
    Start-Sleep -Milliseconds $WaitMs
    $n = $p.BytesToRead
    $txt = ''
    if ($n -gt 0) { $txt = $p.ReadExisting() }
    $p.Close()
    'OPEN_OK bytes={0}' -f $n
    'TEXT=' + ($txt -replace "`r",'<CR>' -replace "`n",'<LF>')
} catch {
    'ERR: ' + $_.Exception.Message
}
