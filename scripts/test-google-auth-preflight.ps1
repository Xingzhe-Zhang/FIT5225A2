$ErrorActionPreference = "Stop"

$ScriptUnderTest = Join-Path $PSScriptRoot "google-auth-preflight.ps1"
if (-not (Test-Path -LiteralPath $ScriptUnderTest)) {
    throw "Missing preflight script: $ScriptUnderTest"
}

$PowerShell = (Get-Process -Id $PID).Path

function Invoke-PreflightCase {
    param([hashtable]$Environment)

    $Names = @(
        "TF_VAR_enable_google_provider",
        "TF_VAR_google_client_id",
        "TF_VAR_google_client_secret",
        "TF_VAR_enable_microsoft_provider"
    )
    $Saved = @{}
    foreach ($Name in $Names) {
        $Saved[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
        [Environment]::SetEnvironmentVariable($Name, $Environment[$Name], "Process")
    }

    try {
        $PreviousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $Output = & $PowerShell -NoProfile -ExecutionPolicy Bypass -File $ScriptUnderTest `
            -CognitoDomainPrefix "pba826-group9" 2>&1 | Out-String
        $ExitCode = $LASTEXITCODE
        $ErrorActionPreference = $PreviousErrorActionPreference
        return @{ ExitCode = $ExitCode; Output = $Output }
    }
    finally {
        $ErrorActionPreference = "Stop"
        foreach ($Name in $Names) {
            [Environment]::SetEnvironmentVariable($Name, $Saved[$Name], "Process")
        }
    }
}

$Missing = Invoke-PreflightCase -Environment @{}
if ($Missing.ExitCode -eq 0) {
    throw "Missing-variable case unexpectedly succeeded."
}
foreach ($Name in @("TF_VAR_enable_google_provider", "TF_VAR_google_client_id", "TF_VAR_google_client_secret")) {
    if ($Missing.Output -notmatch [regex]::Escape($Name)) {
        throw "Missing-variable output did not name $Name."
    }
}

$SecretValue = "google-secret-must-not-appear"
$ValidEnvironment = @{
    TF_VAR_enable_google_provider = "true"
    TF_VAR_google_client_id = "1234567890-abcdef123456.apps.googleusercontent.com"
    TF_VAR_google_client_secret = $SecretValue
    TF_VAR_enable_microsoft_provider = "false"
}
$Valid = Invoke-PreflightCase -Environment $ValidEnvironment
if ($Valid.ExitCode -ne 0) {
    throw "Valid Google configuration failed: $($Valid.Output)"
}
if ($Valid.Output -notmatch "Google federation preflight passed") {
    throw "Valid output did not contain the readiness message."
}
if ($Valid.Output -notmatch "/oauth2/idpresponse") {
    throw "Valid output did not contain the Cognito redirect path."
}
if ($Valid.Output -match [regex]::Escape($SecretValue)) {
    throw "Preflight output leaked the Google client secret."
}

$InvalidClient = $ValidEnvironment.Clone()
$InvalidClient["TF_VAR_google_client_id"] = "not-a-google-client"
$Invalid = Invoke-PreflightCase -Environment $InvalidClient
if ($Invalid.ExitCode -eq 0 -or $Invalid.Output -notmatch "Google OAuth Web client ID") {
    throw "Invalid-client case did not fail clearly."
}

$MicrosoftEnabled = $ValidEnvironment.Clone()
$MicrosoftEnabled["TF_VAR_enable_microsoft_provider"] = "true"
$Microsoft = Invoke-PreflightCase -Environment $MicrosoftEnabled
if ($Microsoft.ExitCode -eq 0 -or $Microsoft.Output -notmatch "TF_VAR_enable_microsoft_provider") {
    throw "Microsoft-enabled case did not fail clearly."
}

Write-Output "Google authentication preflight tests passed."
