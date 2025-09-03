Param(
    [Parameter(Mandatory=$true)][string]$SubscriptionId,
    [Parameter(Mandatory=$true)][string]$ResourceGroup,
    [Parameter(Mandatory=$true)][string]$Location,
    [Parameter(Mandatory=$true)][string]$AppName,
    [Parameter(Mandatory=$true)][string]$AzureOpenAIEndpoint,
    [Parameter(Mandatory=$true)][string]$AzureOpenAIDeployment,
    [string]$AppServicePlanSku = "B1",
    [string]$AppServicePlanName
)

# Zips and deploys the repository root to Azure Web App (single app at root)

if (-not $AppServicePlanName) { $AppServicePlanName = "$AppName-plan" }

az account set --subscription $SubscriptionId
az group create --name $ResourceGroup --location $Location --output none

az appservice plan show -g $ResourceGroup -n $AppServicePlanName *> $null
if ($LASTEXITCODE -ne 0) {
  az appservice plan create -g $ResourceGroup -n $AppServicePlanName --sku $AppServicePlanSku --is-linux --output none
}

az webapp show -g $ResourceGroup -n $AppName *> $null
if ($LASTEXITCODE -ne 0) {
  az webapp create -g $ResourceGroup -n $AppName --plan $AppServicePlanName --runtime "PYTHON|3.11" --output none
}

az webapp identity assign -g $ResourceGroup -n $AppName --output none

az webapp config appsettings set -g $ResourceGroup -n $AppName --settings `
  AZURE_OPENAI_ENDPOINT=$AzureOpenAIEndpoint `
  AZURE_OPENAI_DEPLOYMENT=$AzureOpenAIDeployment `
  AZURE_OPENAI_API_VERSION=2024-06-01 `
  WEBSITES_PORT=8000 `
  PYTHONUNBUFFERED=1 `
  SCM_DO_BUILD_DURING_DEPLOYMENT=true `
  > $null

az webapp config set -g $ResourceGroup -n $AppName --startup-file "gunicorn --bind=0.0.0.0:$PORT app:app" --output none

az webapp auth set -g $ResourceGroup -n $AppName --enabled true --action Redirect --unauthenticated-client-action RedirectToLoginPage --output none

$zipPath = Join-Path $PWD "app.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open($zipPath, 'Create')
$rootPath = $PWD.Path
Get-ChildItem -Recurse -File | Where-Object {
  $_.FullName -ne $zipPath -and
  $_.FullName -notmatch "\\.git\\" -and
  $_.FullName -notmatch "\\.venv\\" -and
  $_.FullName -notmatch "flask-azure-openai-app\\" -and
  $_.FullName -notmatch "__pycache__\\"
} | ForEach-Object {
  $rel = $_.FullName.Substring($rootPath.Length + 1)
  [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $_.FullName, $rel)
}
$zip.Dispose()

az webapp deployment source config-zip -g $ResourceGroup -n $AppName --src $zipPath --output none

Write-Host "Deployment complete: https://$AppName.azurewebsites.net"
