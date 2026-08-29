$ErrorActionPreference = "Stop"

$ScriptUnderTest = Join-Path $PSScriptRoot "microsoft-auth-preflight.ps1"
if (-not (Test-Path -LiteralPath $ScriptUnderTest)) {
    throw "Missing preflight script: $ScriptUnderTest"
}

$PowerShell = (Get-Process -Id $PID).Path

function Invoke-PreflightCase {
    param(
        [hashtable]$Environment,
        [string]$DomainPrefix = "pba826-group9"
    )

    $Saved = @{}
    foreach ($Name in $Environment.Keys) {
        $Saved[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
        [Environment]::SetEnvironmentVariable($Name, $Environment[$Name], "Process")
    }

    try {
        $PreviousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $Output = & $PowerShell -NoProfile -ExecutionPolicy Bypass -File $ScriptUnderTest `
            -CognitoDomainPrefix $DomainPrefix 2>&1 | Out-String
        $ExitCode = $LASTEXITCODE
        $ErrorActionPreference = $PreviousErrorActionPreference
        return @{ ExitCode = $ExitCode; Output = $Output }
    }
    finally {
        $ErrorActionPreference = "Stop"
        foreach ($Name in $Environment.Keys) {
            [Environment]::SetEnvironmentVariable($Name, $Saved[$Name], "Process")
        }
    }
}

$VariableNames = @(
    "TF_VAR_enable_microsoft_provider",
    "TF_VAR_microsoft_tenant",
    "TF_VAR_microsoft_client_id",
    "TF_VAR_microsoft_client_secret"
)

$MissingEnvironment = @{}
foreach ($Name in $VariableNames) {
    $MissingEnvironment[$Name] = $null
}
$Missing = Invoke-PreflightCase -Environment $MissingEnvironment
if ($Missing.ExitCode -eq 0) {
    throw "Missing-variable case unexpectedly succeeded."
}
foreach ($Name in $VariableNames) {
    if ($Missing.Output -notmatch [regex]::Escape($Name)) {
        throw "Missing-variable output did not name $Name."
    }
}

$SecretValue = "preflight-secret-must-not-appear"
$ValidEnvironment = @{
    TF_VAR_enable_microsoft_provider = "true"
    TF_VAR_microsoft_tenant = "common"
    TF_VAR_microsoft_client_id = "11111111-2222-4333-8444-555555555555"
    TF_VAR_microsoft_client_secret = $SecretValue
}
$Valid = Invoke-PreflightCase -Environment $ValidEnvironment
if ($Valid.ExitCode -ne 0) {
    throw "Valid Microsoft configuration failed: $($Valid.Output)"
}
if ($Valid.Output -notmatch "Microsoft federation preflight passed") {
    throw "Valid output did not contain the readiness message."
}
if ($Valid.Output -notmatch "/oauth2/idpresponse") {
    throw "Valid output did not contain the Cognito redirect path."
}
if ($Valid.Output -match [regex]::Escape($SecretValue)) {
    throw "Preflight output leaked the Microsoft client secret."
}

$DisabledEnvironment = $ValidEnvironment.Clone()
$DisabledEnvironment["TF_VAR_enable_microsoft_provider"] = "false"
$Disabled = Invoke-PreflightCase -Environment $DisabledEnvironment
if ($Disabled.ExitCode -eq 0) {
    throw "Disabled-provider case unexpectedly succeeded."
}
if ($Disabled.Output -notmatch "TF_VAR_enable_microsoft_provider") {
    throw "Disabled-provider output did not identify the invalid variable."
}

Write-Output "Microsoft authentication preflight tests passed."
