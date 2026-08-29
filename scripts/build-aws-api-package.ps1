[CmdletBinding()]
param([string]$OutputPath)

$ErrorActionPreference = "Stop"
$Python = (Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$TaskArguments = @((Join-Path $PSScriptRoot "project_tasks.py"), "build-aws-api")
if (-not [string]::IsNullOrWhiteSpace($OutputPath)) { $TaskArguments += @("--output", $OutputPath) }
& $Python @TaskArguments
exit $LASTEXITCODE
