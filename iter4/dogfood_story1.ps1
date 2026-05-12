$ErrorActionPreference = "Continue"
$env:ANTHROPIC_API_KEY = (Get-Content C:\Users\nickt\Desktop\.anthropic_key.txt -Raw).Trim()
$env:SM_TEST_LOG_PATH = "C:\Users\nickt\Desktop\sm-tool\iter4\iter4_log.jsonl"
$env:PYTHONIOENCODING = "utf-8"
Set-Location C:\Users\nickt\Desktop\sm-tool

$story_id = "6412d66eecfd472f951851c29bfd2472"

Write-Host "=== sprint-cut 6 (all 6 stories in Sprint 1) ==="
python -m sm sprint-cut 6
Write-Host "rc=$LASTEXITCODE"

Write-Host ""
Write-Host "=== start story 1 ==="
python -m sm start $story_id
Write-Host "rc=$LASTEXITCODE"

Write-Host ""
Write-Host "=== execute story 1 (DOGFOOD: 3 REAL SDK calls + file materialization) ==="
$exec_start = Get-Date
python -m sm execute $story_id
$rc_exec = $LASTEXITCODE
$exec_dur = ((Get-Date) - $exec_start).TotalSeconds
Write-Host "rc=$rc_exec  duration=${exec_dur}s"

Write-Host ""
Write-Host "=== status ==="
python -m sm status
Write-Host "rc=$LASTEXITCODE"

Write-Host ""
Write-Host "=== materialized files (if any) ==="
Get-ChildItem C:\Users\nickt\Desktop\sm-tool\tests -Filter "test_6412d66e*.py" -ErrorAction SilentlyContinue | Select-Object Name, Length
Get-ChildItem C:\Users\nickt\Desktop\sm-tool\sm.py.candidate -ErrorAction SilentlyContinue | Select-Object Name, Length
Get-ChildItem C:\Users\nickt\Desktop\sm-tool\sm.py.candidate.diff -ErrorAction SilentlyContinue | Select-Object Name, Length
