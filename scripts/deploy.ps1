<#
.SYNOPSIS
End-to-end idempotent deployment for the Flask app to Azure App Service (Linux) with Easy Auth (Google).

.DESCRIPTION
Performs:
1) Resource Group creation
2) Bicep deployment for App Service Plan, Web App, Easy Auth Google config
3) App settings (including Azure OpenAI)
4) Optional: set GOOGLE client secret app setting value
5) Zip deploy application code

Secrets are passed as parameters or set after deployment. Prefer Azure Key Vault references for production.
#>

[CmdletBinding()]Param(
    [Parameter(Mandatory=$true)][string]$SubscriptionId,
    [Parameter(Mandatory=$true)][string]$ResourceGroup,
    [Parameter(Mandatory=$true)][string]$Location,
    [Parameter(Mandatory=$true)][string]$AppName,

    # Google OAuth
    [Parameter(Mandatory=$true)][string]$GoogleClientId,
    [Parameter(Mandatory=$false)][string]$GoogleClientSecret, # optional; can be set later
    [string]$GoogleClientSecretSettingName = 'GOOGLE_PROVIDER_AUTHENTICATION_SECRET',

  # Key Vault
  [Parameter(Mandatory=$true)][string]$KeyVaultName,

    # App settings (sample for Azure OpenAI)
    [Parameter(Mandatory=$true)][string]$AzureOpenAIEndpoint,
    [Parameter(Mandatory=$true)][string]$AzureOpenAIDeployment,
    [string]$FlaskSecretKey = '',

    # Allowlist
    [string]$AllowedEmails = '', # comma-separated list

  # Optional explicit AOAI resource ID to assign RBAC on (overrides discovery)
  [string]$AzureOpenAIResourceId,

    # Plan SKU
    [string]$Sku = 'B1'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Write-Host "Selecting subscription..."
az account set --subscription $SubscriptionId | Out-Null

Write-Host "Ensuring resource group '$ResourceGroup' in '$Location'..."
az group create --name $ResourceGroup --location $Location --output none

$bicepFile = Join-Path $PSScriptRoot '..' | Join-Path -ChildPath 'infra' | Join-Path -ChildPath 'main.bicep'
if (-not (Test-Path $bicepFile)) { throw "Bicep file not found: $bicepFile" }

$extraSettings = @{
  AZURE_OPENAI_ENDPOINT = $AzureOpenAIEndpoint
  AZURE_OPENAI_DEPLOYMENT = $AzureOpenAIDeployment
}
if ($FlaskSecretKey -and $FlaskSecretKey.Trim().Length -gt 0) {
  $extraSettings.FLASK_SECRET_KEY = $FlaskSecretKey
}

Write-Host "Preparing deployment parameters..."
$tempParams = Join-Path $env:TEMP ("$AppName-params.json")
$paramsObject = [ordered]@{
  '$schema' = 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#'
  contentVersion = '1.0.0.0'
  parameters = @{
    location = @{ value = $Location }
    appName  = @{ value = $AppName }
    sku      = @{ value = $Sku }
    googleClientId = @{ value = $GoogleClientId }
  googleClientSecret = @{ value = ($GoogleClientSecret ?? '') }
  keyVaultName = @{ value = $KeyVaultName }
    allowedEmails = @{ value = $AllowedEmails }
    extraAppSettings = @{ value = $extraSettings }
  }
}
$paramsJson = ($paramsObject | ConvertTo-Json -Depth 10)
Set-Content -Path $tempParams -Value $paramsJson -Encoding UTF8

Write-Host "Previewing Bicep deployment (what-if)..."
az deployment group what-if `
  -g $ResourceGroup `
  -n "$AppName-deploy" `
  --template-file $bicepFile `
  --parameters "@$tempParams" 2>$null | Out-Null

Write-Host "Deploying infrastructure..."
az deployment group create `
  -g $ResourceGroup `
  -n "$AppName-deploy" `
  --template-file $bicepFile `
  --parameters "@$tempParams" | Out-Null

if (-not $GoogleClientSecret -or $GoogleClientSecret.Trim().Length -eq 0) {
  Write-Warning "GoogleClientSecret not supplied. Key Vault secret for google-client-secret will be empty. Update it in Key Vault if needed."
}

Write-Host "Configuring startup command..."
az webapp config set -g $ResourceGroup -n $AppName --startup-file 'gunicorn --bind=0.0.0.0:$PORT app:app' --output none

Write-Host "Creating deployment zip..."
# Always package from repository root (parent of scripts folder) so templates/ and static/ are included
$repoRoot = (Join-Path $PSScriptRoot '..' | Resolve-Path).Path
$zipPath = Join-Path $repoRoot "app.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

# Include essential app files and folders
$include = @('app.py','auth.py','azure_openai_client.py','chunking.py','i18n.py','requirements.txt','startup.cmd','web.config','templates','static')
Push-Location $repoRoot
try {
  Compress-Archive -Path $include -DestinationPath $zipPath -Force
} finally {
  Pop-Location
}

Write-Host "Deploying zip to Web App using 'az webapp deploy'..."
# Brief delay to avoid SCM restart conflicts with immediate config changes
Start-Sleep -Seconds 5
az webapp deploy --resource-group $ResourceGroup --name $AppName --type zip --src-path $zipPath --async false --output table | Out-Null

Write-Host "Deployment complete: https://$AppName.azurewebsites.net"

# Try to assign AOAI RBAC: Cognitive Services OpenAI User to the web app identity
try {
  Write-Host "Attempting to assign 'Cognitive Services OpenAI User' role to app identity on Azure OpenAI resource..."
  # Extract app principal id
  $principalId = az webapp identity show -g $ResourceGroup -n $AppName --query principalId -o tsv
  if (-not $principalId) { throw 'Failed to get web app principalId' }

  $aoaiId = $null
  if ($AzureOpenAIResourceId -and $AzureOpenAIResourceId.Trim().Length -gt 0) {
    $aoaiId = $AzureOpenAIResourceId
    Write-Host "Using provided Azure OpenAI resource ID: $aoaiId"
  } else {
    # Attempt to infer AOAI account resource from endpoint host (best-effort)
    # Endpoint formats can vary; we accept manual assignment if inference fails.
  # no-op; rely on endpoint string matching in subsequent query
    # Best-effort: look up OpenAI accounts in subscription and match by endpoint
    $aoaiId = az cognitiveservices account list --subscription $SubscriptionId --query "[?properties.endpoints[?contains(@, '$($AzureOpenAIEndpoint)')]].id | [0]" -o tsv
    if (-not $aoaiId) {
      # Fallback: get first OpenAI account in same RG
      $aoaiId = az cognitiveservices account list -g $ResourceGroup --query "[?kind=='OpenAI'].id | [0]" -o tsv
    }
  }
  if ($aoaiId) {
    $roleDefId = "/subscriptions/$SubscriptionId/providers/Microsoft.Authorization/roleDefinitions/5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"
    az role assignment create --assignee-object-id $principalId --assignee-principal-type ServicePrincipal --role "$roleDefId" --scope "$aoaiId" | Out-Null
    Write-Host "Assigned Cognitive Services OpenAI User to app identity on: $aoaiId"
  } else {
    Write-Warning "Could not find Azure OpenAI account to assign RBAC. Assign manually if needed."
  }
} catch {
  Write-Warning "AOAI RBAC assignment attempt failed: $_"
}
