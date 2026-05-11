$ErrorActionPreference = "Continue"
$env:ANTHROPIC_API_KEY = (Get-Content C:\Users\nickt\Desktop\.anthropic_key.txt -Raw).Trim()
$env:SM_TEST_LOG_PATH = "C:\Users\nickt\Desktop\sm-tool\iter2\smoke_log.jsonl"
$env:PYTHONIOENCODING = "utf-8"
Set-Location C:\Users\nickt\Desktop\sm-tool

Write-Host "=== STEP 1: ingest ==="
python -m sm ingest C:\Users\nickt\Desktop\sm-tool\iter2\smoke_handoff.json
Write-Host "rc=$LASTEXITCODE"

Write-Host ""
Write-Host "=== STEP 2: decompose (REAL Anthropic SDK call) ==="
$decompose_start = Get-Date
python -m sm decompose
$rc_decompose = $LASTEXITCODE
$decompose_dur = ((Get-Date) - $decompose_start).TotalSeconds
Write-Host "rc=$rc_decompose  duration=${decompose_dur}s"

Write-Host ""
Write-Host "=== STEP 3: status (after decompose) ==="
python -m sm status
Write-Host "rc=$LASTEXITCODE"
