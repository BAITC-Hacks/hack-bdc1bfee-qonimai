$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $taskRoot
$taskPython = Join-Path $taskRoot '.venv/Scripts/python.exe'
$taskServer = Join-Path $taskRoot 'runtime/llama/llama-server.exe'
$taskModel = Join-Path $taskRoot 'models/qwen/Qwen3-4B-Q4_K_M.gguf'
foreach ($taskRequired in @($taskPython, $taskServer, $taskModel)) {
    if (-not (Test-Path -LiteralPath $taskRequired)) {
        throw "Missing local component. Complete installation and scripts/setup_models.py first."
    }
}
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
$env:DO_NOT_TRACK = '1'
$env:QONIMAI_ASR_MODEL = Join-Path $taskRoot 'models/whisper-small'
$env:QONIMAI_SPEAKER_MODEL = Join-Path $taskRoot 'models/speaker/wespeaker_en_voxceleb_resnet34_LM.onnx'
$env:QONIMAI_ANALYSIS_URL = 'http://127.0.0.1:8081'
$taskHealthy = $false
try {
    $taskHealth = Invoke-RestMethod 'http://127.0.0.1:8081/health' -TimeoutSec 2
    $taskHealthy = $taskHealth.status -eq 'ok'
} catch { }
if (-not $taskHealthy) {
    $taskArguments = @('-m', ('"' + $taskModel + '"'), '--host', '127.0.0.1', '--port', '8081', '--alias', 'local', '--ctx-size', '8192', '--threads', '6', '--parallel', '1', '--n-gpu-layers', '0', '--reasoning', 'off', '--reasoning-budget', '0', '--log-disable')
    $taskProcess = Start-Process -FilePath $taskServer -ArgumentList $taskArguments -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput 'runtime/llama.stdout.log' -RedirectStandardError 'runtime/llama.stderr.log'
    $taskProcess.Id | Set-Content 'runtime/llama.pid'
    for ($taskAttempt=0; $taskAttempt -lt 60; $taskAttempt++) {
        Start-Sleep -Seconds 1
        try {
            $taskHealth = Invoke-RestMethod 'http://127.0.0.1:8081/health' -TimeoutSec 2
            if ($taskHealth.status -eq 'ok') { $taskHealthy=$true; break }
        } catch { }
        if ($taskProcess.HasExited) { throw 'Local model server exited. Check runtime/llama.stderr.log.' }
    }
    if (-not $taskHealthy) { throw 'Local model server is not ready. Check runtime/llama.stderr.log.' }
}
Write-Host 'QonimAI: http://127.0.0.1:8501 (local only)'
Write-Host 'Ctrl+C stops the interface. The local model server PID is in runtime/llama.pid.'
& $taskPython -X utf8 -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
