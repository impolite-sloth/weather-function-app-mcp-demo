targetScope = 'resourceGroup'

@minLength(1)
@maxLength(24)
@description('Short environment label used to generate deterministic resource names.')
param environmentName string = 'dev'

@description('Deployment location for all resources.')
param location string = resourceGroup().location

@description('Optional Function App name override. Leave empty to auto-generate.')
param functionAppName string = ''

@description('Optional App Service plan name override. Leave empty to auto-generate.')
param functionPlanName string = ''

@description('Optional storage account name override. Must be globally unique if provided.')
param storageAccountName string = ''

@description('Optional Log Analytics workspace name override.')
param logAnalyticsName string = ''

@description('Optional Application Insights name override.')
param applicationInsightsName string = ''

@allowed([
	'python'
	'dotnet-isolated'
	'java'
	'node'
	'powerShell'
])
@description('Language runtime for the Flex Consumption function app.')
param functionAppRuntime string = 'python'

@allowed([
	'3.10'
	'3.11'
	'3.12'
	'7.4'
	'8.0'
	'9.0'
	'10'
	'11'
	'17'
	'20'
	'21'
	'22'
])
@description('Runtime version for the selected language runtime.')
param functionAppRuntimeVersion string = '3.11'

@minValue(40)
@maxValue(1000)
@description('Maximum number of Flex Consumption instances.')
param maximumInstanceCount int = 100

@allowed([
	512
	2048
	4096
])
@description('Per-instance memory size in MB for Flex Consumption.')
param instanceMemoryMB int = 2048

@description('Enable zone redundancy for supported resources where applicable.')
param zoneRedundant bool = false

var token = toLower(uniqueString(subscription().id, resourceGroup().id, environmentName, location))

var functionAppNameResolved = !empty(functionAppName)
	? functionAppName
	: 'func-${take(token, 18)}'

var functionPlanNameResolved = !empty(functionPlanName)
	? functionPlanName
	: 'plan-${take(token, 18)}'

var storageAccountNameResolved = !empty(storageAccountName)
	? storageAccountName
	: 'st${take(token, 22)}'

var logAnalyticsNameResolved = !empty(logAnalyticsName)
	? logAnalyticsName
	: 'log-${take(token, 18)}'

var applicationInsightsNameResolved = !empty(applicationInsightsName)
	? applicationInsightsName
	: 'appi-${take(token, 18)}'

var deploymentStorageContainerName = 'app-package-${take(token, 24)}'

var tags = {
	'azd-env-name': environmentName
	workload: 'azure-functions-flex'
}

var roleDefinitionIds = {
	storageBlobDataOwner: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
	storageQueueDataContributor: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '974c5e8b-45b9-4653-ba55-5f855dd0fb88')
	storageTableDataContributor: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3')
	monitoringMetricsPublisher: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '3913510d-42f4-4e42-8a64-420c390055eb')
}

module logAnalytics 'br/public:avm/res/operational-insights/workspace:0.11.1' = {
	name: 'loganalytics-${token}'
	params: {
		name: logAnalyticsNameResolved
		location: location
		tags: tags
		dataRetention: 30
	}
}

module applicationInsights 'br/public:avm/res/insights/component:0.6.0' = {
	name: 'appinsights-${token}'
	params: {
		name: applicationInsightsNameResolved
		location: location
		tags: tags
		workspaceResourceId: logAnalytics.outputs.resourceId
		disableLocalAuth: true
	}
}

module storage 'br/public:avm/res/storage/storage-account:0.25.0' = {
	name: 'storage-${token}'
	params: {
		name: storageAccountNameResolved
		location: location
		tags: tags
		allowBlobPublicAccess: false
		allowSharedKeyAccess: false
		dnsEndpointType: 'Standard'
		minimumTlsVersion: 'TLS1_2'
		publicNetworkAccess: 'Enabled'
		networkAcls: {
			defaultAction: 'Allow'
			bypass: 'AzureServices'
		}
		blobServices: {
			containers: [
				{
					name: deploymentStorageContainerName
				}
			]
		}
		queueServices: {}
		tableServices: {}
	}
}

module appServicePlan 'br/public:avm/res/web/serverfarm:0.1.1' = {
	name: 'plan-${token}'
	params: {
		name: functionPlanNameResolved
		location: location
		tags: tags
		reserved: true
		zoneRedundant: zoneRedundant
		sku: {
			name: 'FC1'
			tier: 'FlexConsumption'
		}
	}
}

module functionApp 'br/public:avm/res/web/site:0.16.0' = {
	name: 'functionapp-${token}'
	params: {
		kind: 'functionapp,linux'
		name: functionAppNameResolved
		location: location
		tags: union(tags, {
			'azd-service-name': 'api'
		})
		serverFarmResourceId: appServicePlan.outputs.resourceId
		managedIdentities: {
			systemAssigned: true
		}
		functionAppConfig: {
			deployment: {
				storage: {
					type: 'blobContainer'
					value: '${storage.outputs.primaryBlobEndpoint}${deploymentStorageContainerName}'
					authentication: {
						type: 'SystemAssignedIdentity'
					}
				}
			}
			scaleAndConcurrency: {
				maximumInstanceCount: maximumInstanceCount
				instanceMemoryMB: instanceMemoryMB
			}
			runtime: {
				name: functionAppRuntime
				version: functionAppRuntimeVersion
			}
		}
		siteConfig: {
			alwaysOn: false
		}
		configs: [
			{
				name: 'appsettings'
				properties: {
					AzureWebJobsStorage__credential: 'managedidentity'
					AzureWebJobsStorage__blobServiceUri: 'https://${storage.outputs.name}.blob.${environment().suffixes.storage}'
					AzureWebJobsStorage__queueServiceUri: 'https://${storage.outputs.name}.queue.${environment().suffixes.storage}'
					AzureWebJobsStorage__tableServiceUri: 'https://${storage.outputs.name}.table.${environment().suffixes.storage}'
					APPLICATIONINSIGHTS_CONNECTION_STRING: applicationInsights.outputs.connectionString
					APPLICATIONINSIGHTS_AUTHENTICATION_STRING: 'Authorization=AAD'
				}
			}
		]
	}
}

module storageBlobDataOwner 'br/public:avm/ptn/authorization/resource-role-assignment:0.1.2' = {
	name: 'storage-blob-owner-${token}'
	params: {
		resourceId: storage.outputs.resourceId
		roleDefinitionId: roleDefinitionIds.storageBlobDataOwner
		roleName: 'Storage Blob Data Owner'
		principalId: functionApp.outputs.?systemAssignedMIPrincipalId ?? ''
		principalType: 'ServicePrincipal'
		description: 'Grants Function App managed identity access to blob storage.'
	}
}

module storageQueueDataContributor 'br/public:avm/ptn/authorization/resource-role-assignment:0.1.2' = {
	name: 'storage-queue-contributor-${token}'
	params: {
		resourceId: storage.outputs.resourceId
		roleDefinitionId: roleDefinitionIds.storageQueueDataContributor
		roleName: 'Storage Queue Data Contributor'
		principalId: functionApp.outputs.?systemAssignedMIPrincipalId ?? ''
		principalType: 'ServicePrincipal'
		description: 'Grants Function App managed identity access to queue storage.'
	}
}

module storageTableDataContributor 'br/public:avm/ptn/authorization/resource-role-assignment:0.1.2' = {
	name: 'storage-table-contributor-${token}'
	params: {
		resourceId: storage.outputs.resourceId
		roleDefinitionId: roleDefinitionIds.storageTableDataContributor
		roleName: 'Storage Table Data Contributor'
		principalId: functionApp.outputs.?systemAssignedMIPrincipalId ?? ''
		principalType: 'ServicePrincipal'
		description: 'Grants Function App managed identity access to table storage.'
	}
}

module appInsightsMetricsPublisher 'br/public:avm/ptn/authorization/resource-role-assignment:0.1.2' = {
	name: 'appi-metrics-publisher-${token}'
	params: {
		resourceId: applicationInsights.outputs.resourceId
		roleDefinitionId: roleDefinitionIds.monitoringMetricsPublisher
		roleName: 'Monitoring Metrics Publisher'
		principalId: functionApp.outputs.?systemAssignedMIPrincipalId ?? ''
		principalType: 'ServicePrincipal'
		description: 'Allows managed identity-based telemetry to Application Insights.'
	}
}

output functionAppResourceName string = functionApp.outputs.name
output functionAppResourceId string = functionApp.outputs.resourceId
output applicationInsightsConnectionString string = applicationInsights.outputs.connectionString
