param(
    [int]$Seconds = 240,
    [string]$Out = "S:\apps\llm\agentwork\qwen3\board\serial_capture.log"
)
$port = New-Object System.IO.Ports.SerialPort("COM6", 115200, "None", 8, "One")
$port.ReadTimeout = 200
$port.Open()
$sw = [System.IO.StreamWriter]::new($Out, $true)
$sw.WriteLine("=== capture start " + (Get-Date).ToString("o") + " ===")
$sw.Flush()
$deadline = (Get-Date).AddSeconds($Seconds)
while ((Get-Date) -lt $deadline) {
    try {
        $line = $port.ReadLine()
        $sw.WriteLine((Get-Date).ToString("HH:mm:ss") + " | " + $line)
        $sw.Flush()
    } catch [System.TimeoutException] {
    } catch {
        break
    }
}
$sw.WriteLine("=== capture end " + (Get-Date).ToString("o") + " ===")
$sw.Close()
$port.Close()
Write-Output ("serial capture done -> " + $Out)
