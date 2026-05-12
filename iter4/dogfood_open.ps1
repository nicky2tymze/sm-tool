$ErrorActionPreference = "Continue"
$env:ANTHROPIC_API_KEY = (Get-Content C:\Users\nickt\Desktop\.anthropic_key.txt -Raw).Trim()
$env:SM_TEST_LOG_PATH = "C:\Users\nickt\Desktop\sm-tool\iter4\iter4_log.jsonl"
$env:PYTHONIOENCODING = "utf-8"
Set-Location C:\Users\nickt\Desktop\sm-tool

Remove-Item $env:SM_TEST_LOG_PATH -ErrorAction SilentlyContinue

Write-Host "=== STEP 1: ingest iter4 handoff ==="
python -m sm ingest C:\Users\nickt\Desktop\sm-tool\iter4\handoff.json
Write-Host "rc=$LASTEXITCODE"

Write-Host ""
Write-Host "=== STEP 2: decompose (REAL SDK; iter3-autonomy context now bundled in user message) ==="
$decompose_start = Get-Date
python -m sm decompose
$rc_decompose = $LASTEXITCODE
$decompose_dur = ((Get-Date) - $decompose_start).TotalSeconds
Write-Host "rc=$rc_decompose  duration=${decompose_dur}s"

Write-Host ""
Write-Host "=== STEP 3: status ==="
python -m sm status
Write-Host "rc=$LASTEXITCODE"
