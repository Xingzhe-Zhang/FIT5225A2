[CmdletBinding()]
param(
    [ValidateSet("All", "Health", "Auth", "Upload", "Query", "Tag", "Delete", "Notification", "Logs")]
    [string]$Check = "Health",
    [switch]$AllowMutation
)

$ErrorActionPreference = "Stop"

function Get-PbaEnvironmentValue {
    param([Parameter(Mandatory)][string]$Name)

    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "Required environment variable $Name is not set."
    }
    return $value.Trim()
}

function Get-PbaApiBaseUrl {
    $baseUrl = Get-PbaEnvironmentValue -Name "PBA_API_BASE_URL"
    if (-not $baseUrl.StartsWith("https://") -and -not $baseUrl.StartsWith("http://localhost")) {
        throw "PBA_API_BASE_URL must use HTTPS, except for localhost verification."
    }
    return $baseUrl.TrimEnd("/")
}

function Get-PbaAuthorizationHeaders {
    $accessToken = Get-PbaEnvironmentValue -Name "PBA_ACCESS_TOKEN"
    return @{ Authorization = "Bearer $accessToken"; "x-request-id" = [guid]::NewGuid().ToString() }
}

function Invoke-PbaJsonRequest {
    param(
        [Parameter(Mandatory)][string]$Method,
        [Parameter(Mandatory)][string]$Path,
        [object]$Body,
        [switch]$Authenticated
    )

    $parameters = @{
        Method      = $Method
        Uri         = "$(Get-PbaApiBaseUrl)$Path"
        ContentType = "application/json"
    }
    if ($Authenticated) {
        $parameters.Headers = Get-PbaAuthorizationHeaders
    }
    if ($null -ne $Body) {
        $parameters.Body = $Body | ConvertTo-Json -Depth 8 -Compress
    }
    return Invoke-RestMethod @parameters
}

function Assert-PbaMutationAllowed {
    param([Parameter(Mandatory)][string]$Name)

    if (-not $AllowMutation) {
        throw "$Name changes cloud data. Re-run with -AllowMutation after reviewing the target environment."
    }
}

function Test-PbaHealth {
    $response = Invoke-PbaJsonRequest -Method "GET" -Path "/health"
    if ($response.status -ne "ok") { throw "Health response did not report ok." }
    Write-Host "Health: PASS"
}

function Test-PbaAuth {
    $unauthorized = $false
    try {
        Invoke-PbaJsonRequest -Method "POST" -Path "/queries/species" -Body @{ species = "dingo" } | Out-Null
    }
    catch {
        $status = [int]$_.Exception.Response.StatusCode
        $unauthorized = $status -in 401, 403
    }
    if (-not $unauthorized) { throw "Protected endpoint did not reject an anonymous request." }

    Invoke-PbaJsonRequest -Method "POST" -Path "/queries/species" -Body @{ species = "dingo" } -Authenticated | Out-Null
    Write-Host "Auth: PASS (anonymous rejected; bearer token accepted)"
}

function Test-PbaUpload {
    Assert-PbaMutationAllowed -Name "Upload verification"
    $filePath = Get-PbaEnvironmentValue -Name "PBA_TEST_FILE"
    if (-not (Test-Path -LiteralPath $filePath -PathType Leaf)) { throw "PBA_TEST_FILE does not exist." }
    $item = Get-Item -LiteralPath $filePath
    $checksum = (Get-FileHash -LiteralPath $filePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $extension = $item.Extension.ToLowerInvariant()
    $mediaType = if ($extension -in ".mp4", ".mov") { "video" } else { "image" }
    $reservation = Invoke-PbaJsonRequest -Method "POST" -Path "/uploads/reservations" -Authenticated -Body @{
        file_name  = $item.Name
        media_type = $mediaType
        size_bytes = $item.Length
        sha256     = $checksum
    }
    if (-not $reservation.duplicate) {
        $contentType = if ($extension -in ".jpg", ".jpeg") { "image/jpeg" }
            elseif ($extension -eq ".png") { "image/png" }
            elseif ($extension -eq ".mov") { "video/quicktime" }
            else { "video/mp4" }
        Invoke-WebRequest -Method Put -Uri $reservation.upload_url -InFile $filePath -ContentType $contentType `
            -Headers @{ "x-amz-meta-sha256" = $checksum } | Out-Null
    }
    Write-Host "Upload: PASS (media_id=$($reservation.media_id), duplicate=$($reservation.duplicate))"
}

function Test-PbaQuery {
    $species = [Environment]::GetEnvironmentVariable("PBA_TEST_SPECIES")
    if ([string]::IsNullOrWhiteSpace($species)) { $species = "dingo" }
    $response = Invoke-PbaJsonRequest -Method "POST" -Path "/queries/species" -Authenticated -Body @{ species = $species }
    Write-Host "Query: PASS (results=$(@($response.results).Count))"
}

function Test-PbaTag {
    Assert-PbaMutationAllowed -Name "Tag verification"
    $mediaUrl = Get-PbaEnvironmentValue -Name "PBA_TEST_MEDIA_URL"
    Invoke-PbaJsonRequest -Method "POST" -Path "/media/tags" -Authenticated -Body @{
        urls      = @($mediaUrl)
        tags      = @("deployment-smoke-test")
        operation = 1
    } | Out-Null
    Write-Host "Tag: PASS"
}

function Test-PbaDelete {
    Assert-PbaMutationAllowed -Name "Delete verification"
    $mediaUrl = Get-PbaEnvironmentValue -Name "PBA_DELETE_MEDIA_URL"
    Invoke-PbaJsonRequest -Method "DELETE" -Path "/media" -Authenticated -Body @{ urls = @($mediaUrl) } | Out-Null
    Write-Host "Delete: PASS"
}

function Test-PbaNotification {
    Assert-PbaMutationAllowed -Name "Notification verification"
    $email = Get-PbaEnvironmentValue -Name "PBA_NOTIFICATION_EMAIL"
    Invoke-PbaJsonRequest -Method "POST" -Path "/subscriptions" -Authenticated -Body @{
        email = $email
        tags  = @("dingo")
    } | Out-Null
    Write-Host "Notification: PASS (SNS confirmation and receipt remain HUMAN REQUIRED)"
}

function Test-PbaLogs {
    $requestId = [guid]::NewGuid().ToString()
    Invoke-RestMethod -Method Get -Uri "$(Get-PbaApiBaseUrl)/health" -Headers @{ "x-request-id" = $requestId } | Out-Null
    Write-Host "Logs: request sent; verify request ID $requestId in CloudWatch and Application Insights."
}

$checks = if ($Check -eq "All") {
    @("Health", "Auth", "Upload", "Query", "Tag", "Delete", "Notification", "Logs")
} else {
    @($Check)
}

foreach ($selectedCheck in $checks) {
    & "Test-Pba$selectedCheck"
}
