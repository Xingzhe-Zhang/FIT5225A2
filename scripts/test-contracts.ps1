$ErrorActionPreference = "Stop"
$Python = (Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
& $Python (Join-Path $PSScriptRoot "project_tasks.py") test-contracts
exit $LASTEXITCODE
