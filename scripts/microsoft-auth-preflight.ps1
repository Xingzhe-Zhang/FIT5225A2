param(
    [Parameter(Mandatory = $true)]
    [string]$CognitoDomainPrefix,

    [string]$AwsRegion = "ap-southeast-2"
)

$ErrorActionPreference = "Stop"

$RequiredVariables = @(
    "TF_VAR_enable_microsoft_provider",
    "TF_VAR_microsoft_tenant",
    "TF_VAR_microsoft_client_id",
    "TF_VAR_microsoft_client_secret"
)

$Errors = [System.Collections.Generic.List[string]]::new()
foreach ($Name in $RequiredVariables) {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($Name, "Process"))) {
        $Errors.Add("Missing required environment variable: $Name")
    }
}

$Enabled = [Environment]::GetEnvironmentVariable("TF_VAR_enable_microsoft_provider", "Process")
if (-not [string]::IsNullOrWhiteSpace($Enabled) -and $Enabled.Trim().ToLowerInvariant() -ne "true") {
    $Errors.Add("TF_VAR_enable_microsoft_provider must be true.")
}

$Tenant = [Environment]::GetEnvironmentVariable("TF_VAR_microsoft_tenant", "Process")
if (-not [string]::IsNullOrWhiteSpace($Tenant) -and $Tenant.Trim().ToLowerInvariant() -ne "common") {
    $Errors.Add("TF_VAR_microsoft_tenant must be common to support personal and organisational accounts.")
}

$ClientId = [Environment]::GetEnvironmentVariable("TF_VAR_microsoft_client_id", "Process")
$ParsedClientId = [Guid]::Empty
if (-not [string]::IsNullOrWhiteSpace($ClientId) -and -not [Guid]::TryParse($ClientId, [ref]$ParsedClientId)) {
    $Errors.Add("TF_VAR_microsoft_client_id must be an Entra application (client) ID GUID.")
}

if ($CognitoDomainPrefix -notmatch '^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$') {
    $Errors.Add("CognitoDomainPrefix must contain lowercase letters, digits or internal hyphens and be at most 63 characters.")
}
if ($AwsRegion -notmatch '^[a-z]{2}(?:-gov)?-[a-z]+-\d$') {
    $Errors.Add("AwsRegion is not a valid AWS region name.")
}

if ($Errors.Count -gt 0) {
    foreach ($Message in $Errors) {
        Write-Output "ERROR: $Message"
    }
    exit 1
}

$RedirectUri = "https://$CognitoDomainPrefix.auth.$AwsRegion.amazoncognito.com/oauth2/idpresponse"
Write-Output "Microsoft federation preflight passed."
Write-Output "Register this exact Entra Web redirect URI: $RedirectUri"
Write-Output "Credentials are present and were not printed."
