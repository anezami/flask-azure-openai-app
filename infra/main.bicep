// infra/main.bicep
// Purpose: Provision an Azure Linux App Service Plan + Web App and configure Easy Auth (V2) with Google provider.
// Notes:
// - Google client secret is supplied via app setting named by 'googleClientSecretSettingName'.
// - Email allowlist is enforced in application code via ALLOWED_EMAILS app setting (comma-separated).
// - This template is idempotent; safe to redeploy.

@description('Location for all resources (e.g., westeurope)')
param location string

@description('Name of the App Service (web app). Must be globally unique.')
param appName string

@description('App Service Plan SKU, e.g., B1, P1v3')
@allowed([
  'F1'
  'B1'
  'B2'
  'B3'
  'P1v3'
  'P2v3'
  'P3v3'
])
param sku string = 'B1'

@description('Google OAuth Client ID')
param googleClientId string

@secure()
@description('Google OAuth Client Secret (secure). Will be stored in Key Vault and referenced by App Settings.')
param googleClientSecret string

@description('Key Vault name to create (must be globally unique within your tenant).')
param keyVaultName string

// App setting name for Google client secret is fixed to avoid dynamic property names in appsettings
var googleClientSecretSettingName = 'GOOGLE_PROVIDER_AUTHENTICATION_SECRET'

@description('Optional: Comma-separated list of allowed email addresses for app-level authorization (enforced by the app). Leave empty to allow all authenticated users.')
param allowedEmails string = ''

@description('Optional: Additional app settings to merge. Keys and values as an object.')
param extraAppSettings object = {}


// Azure OpenAI role assignment is handled in the deployment script to support cross-scope operations.

var planName = '${appName}-plan'

// Versionless Key Vault Secret URIs (always resolve to latest version)
var googleClientSecretUri = '${kv.properties.vaultUri}secrets/google-client-secret'
var googleClientIdUri = '${kv.properties.vaultUri}secrets/google-client-id'

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  sku: {
    name: sku
  }
  kind: 'linux'
  properties: {
    reserved: true // Linux
  }
}

resource site 'Microsoft.Web/sites@2023-12-01' = {
  name: appName
  location: location
  kind: 'app,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      alwaysOn: true
      http20Enabled: true
  minTlsVersion: '1.2'
  keyVaultReferenceIdentity: 'SystemAssigned'
    }
  }
}

// Key Vault to store Google Client Id and Secret
resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enabledForTemplateDeployment: false
    enabledForDeployment: false
    enabledForDiskEncryption: false
    publicNetworkAccess: 'Enabled'
    softDeleteRetentionInDays: 7
  }
}

// Secrets in Key Vault
resource kvGoogleClientId 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  name: 'google-client-id'
  parent: kv
  properties: {
    value: googleClientId
  }
}

resource kvGoogleClientSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  name: 'google-client-secret'
  parent: kv
  properties: {
    value: googleClientSecret
  }
}

// Allow the web app's system-assigned identity to read secrets from this Key Vault
resource kvSecretsUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, site.id, 'kv-secrets-user')
  scope: kv
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6') // Key Vault Secrets User
    principalId: site.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// App Settings
resource appSettings 'Microsoft.Web/sites/config@2023-12-01' = {
  name: 'appsettings'
  parent: site
  properties: union({
    // Runtime
    WEBSITES_PORT: '8000'
    SCM_DO_BUILD_DURING_DEPLOYMENT: 'true'
    PYTHONUNBUFFERED: '1'

    // App-specific
    ALLOWED_EMAILS: allowedEmails

    // Placeholders for Azure OpenAI, etc. Fill via parameters or later.
    AZURE_OPENAI_API_VERSION: '2024-06-01'

    // Google secret is stored in an app setting whose name is provided by googleClientSecretSettingName.
    // Use Key Vault reference (versionless) so it always resolves to the latest secret version.
  GOOGLE_PROVIDER_AUTHENTICATION_SECRET: '@Microsoft.KeyVault(SecretUri=${googleClientSecretUri})'

    // Also expose client id as an app setting (from Key Vault) for app code if needed (versionless)
    GOOGLE_CLIENT_ID: '@Microsoft.KeyVault(SecretUri=${googleClientIdUri})'
  }, extraAppSettings)
}

// Easy Auth v2 configuration with Google as the default provider
resource auth 'Microsoft.Web/sites/config@2023-12-01' = {
  name: 'authsettingsV2'
  parent: site
  properties: {
    platform: {
      enabled: true
      runtimeVersion: '~1' // Authentication/Authorization (Easy Auth) runtime
      configFilePath: ''
    }
    globalValidation: {
      requireAuthentication: true
      unauthenticatedClientAction: 'RedirectToLoginPage'
      redirectToProvider: 'google'
      excludedPaths: [ '/health' ]
    }
    identityProviders: {
      google: {
        enabled: true
        registration: {
          clientId: googleClientId
          // Name of the app setting that contains the client secret value
          clientSecretSettingName: googleClientSecretSettingName
        }
        // Optional: scopes can be customized if needed
        // login: {
        //   scopes: [ 'openid', 'profile', 'email' ]
        // }
      }
    }
    login: {
      // Persist tokens if your app needs to call downstream APIs from server side
      tokenStore: {
        enabled: false
      }
      routes: {
        // Use default login/logout routes
      }
    }
    httpSettings: {
      requireHttps: true
      routes: {
        // default
      }
    }
  }
  dependsOn: [ appSettings ]
}

output webAppName string = site.name
output webAppUrl string = 'https://${site.name}.azurewebsites.net'
output appServicePlanId string = plan.id
output keyVaultName string = kv.name
