[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryUri,
    [string]$Tag = "ml-v1",
    [string]$ModelDirectory
)

$ErrorActionPreference = "Stop"
$Python = (Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$TaskArguments = @(
    (Join-Path $PSScriptRoot "project_tasks.py"),
    "build-push-worker-image",
    $RepositoryUri,
    "--tag",
    $Tag
)
if (-not [string]::IsNullOrWhiteSpace($ModelDirectory)) {
    $TaskArguments += @("--model-directory", $ModelDirectory)
}
& $Python @TaskArguments
exit $LASTEXITCODE
