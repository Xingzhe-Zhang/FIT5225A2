param(
    [Parameter(Mandatory = $true)]
    [string]$CognitoDomainPrefix,

    [string]$AwsRegion = "ap-southeast-2",

    [string]$FrontendOrigin = "http://localhost:5173"
)

$ErrorActionPreference = "Stop"

$RequiredVariables = @(
    "TF_VAR_enable_google_provider",
    "TF_VAR_google_client_id",
    "TF_VAR_google_client_secret"
)

$Errors = [System.Collections.Generic.List[string]]::new()
foreach ($Name in $RequiredVariables) {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($Name, "Process"))) {
        $Errors.Add("Missing required environment variable: $Name")
    }
}

$Enabled = [Environment]::GetEnvironmentVariable("TF_VAR_enable_google_provider", "Process")
if (-not [string]::IsNullOrWhiteSpace($Enabled) -and $Enabled.Trim().ToLowerInvariant() -ne "true") {
    $Errors.Add("TF_VAR_enable_google_provider must be true.")
}

$MicrosoftEnabled = [Environment]::GetEnvironmentVariable("TF_VAR_enable_microsoft_provider", "Process")
if (-not [string]::IsNullOrWhiteSpace($MicrosoftEnabled) -and $MicrosoftEnabled.Trim().ToLowerInvariant() -eq "true") {
    $Errors.Add("TF_VAR_enable_microsoft_provider must be false or unset for the Google-only deployment.")
}

$ClientId = [Environment]::GetEnvironmentVariable("TF_VAR_google_client_id", "Process")
if (
    -not [string]::IsNullOrWhiteSpace($ClientId) -and
    $ClientId.Trim() -notmatch '^[0-9]+-[a-z0-9-]+\.apps\.googleusercontent\.com$'
) {
    $Errors.Add("TF_VAR_google_client_id is not a Google OAuth Web client ID.")
}

if ($CognitoDomainPrefix -notmatch '^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$') {
    $Errors.Add("CognitoDomainPrefix must contain lowercase letters, digits or internal hyphens and be at most 63 characters.")
}
if ($AwsRegion -notmatch '^[a-z]{2}(?:-gov)?-[a-z]+-\d$') {
    $Errors.Add("AwsRegion is not a valid AWS region name.")
}
if ($FrontendOrigin -notmatch '^(https://[^/]+|http://localhost(?::[0-9]+)?)$') {
    $Errors.Add("FrontendOrigin must be HTTPS, except for localhost development URLs.")
}

if ($Errors.Count -gt 0) {
    foreach ($Message in $Errors) {
        Write-Output "ERROR: $Message"
    }
    exit 1
}

$RedirectUri = "https://$CognitoDomainPrefix.auth.$AwsRegion.amazoncognito.com/oauth2/idpresponse"
Write-Output "Google federation preflight passed."
Write-Output "Google OAuth authorised JavaScript origin: $FrontendOrigin"
Write-Output "Google OAuth authorised redirect URI: $RedirectUri"
Write-Output "Credentials are present and were not printed."
