$ErrorActionPreference = "Continue"
$env:ANTHROPIC_API_KEY = (Get-Content C:\Users\nickt\Desktop\.anthropic_key.txt -Raw).Trim()
$env:SM_TEST_LOG_PATH = "C:\Users\nickt\Desktop\sm-tool\iter2\smoke_log.jsonl"
$env:PYTHONIOENCODING = "utf-8"
Set-Location C:\Users\nickt\Desktop\sm-tool

$story_id = "815424a53ec64744a5aad1c4d8feb992"

Write-Host "=== STEP 4: sprint-cut 1 ==="
python -m sm sprint-cut 1
Write-Host "rc=$LASTEXITCODE"

Write-Host ""
Write-Host "=== STEP 5: start $story_id ==="
python -m sm start $story_id
Write-Host "rc=$LASTEXITCODE"

Write-Host ""
Write-Host "=== STEP 6: execute (3 REAL SDK calls -- test_writer + coder + reviewer) ==="
$exec_start = Get-Date
python -m sm execute $story_id
$rc_exec = $LASTEXITCODE
$exec_dur = ((Get-Date) - $exec_start).TotalSeconds
Write-Host "rc=$rc_exec  duration=${exec_dur}s"

Write-Host ""
Write-Host "=== STEP 7: status (final) ==="
python -m sm status
Write-Host "rc=$LASTEXITCODE"
