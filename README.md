# Weather Function App Remote

Standalone Azure Functions MCP project for a weather toolset, plus a local Python client that talks to the deployed Function App through the MCP endpoint.

## Project Layout

```text
weather-function-app-remote/
  app/
    dist/
      index.html
  client/
    .env.example
    remote_client.py
    requirements.txt
  .funcignore
  .gitignore
  function_app.py
  host.json
  infra/
    main.bicep
  local.settings.json.example
  README.md
  requirements-client.txt
  requirements.txt
  tests/
  weather_service.py
```

## What This Project Contains

- `function_app.py`: Azure Functions MCP tool and MCP resource definitions.
- `weather_service.py`: Weather data access layer using Open-Meteo.
- `app/dist/index.html`: MCP App widget UI returned by the resource trigger.
- `client/remote_client.py`: Local Python client that connects to the deployed Function App MCP endpoint and uses an Azure AI Foundry agent to decide when to call the tools.
- `infra/main.bicep`: Azure infrastructure deployment template for Flex Consumption Function App hosting.
- `tests/`: Test folder for unit and integration checks.

## Prerequisites

- Python 3.11 or 3.12 (the Bicep template defaults to 3.11 unless overridden).
- Azure Functions Core Tools 4.0.7030 or later.
- Azure CLI.
- An Azure subscription.
- An Azure AI Foundry project with a deployed chat model.
- For local Function App execution: Azurite or another valid `AzureWebJobsStorage` connection.

## Local Function App Setup

1. Create and activate a virtual environment.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install Function App dependencies.

```powershell
pip install -r requirements.txt
```

3. Create a local settings file.

```powershell
Copy-Item local.settings.json.example local.settings.json
```

4. Start Azurite if you are using `UseDevelopmentStorage=true`.

5. Start the local Functions host.

```powershell
func start
```

The MCP endpoint is then available locally at:

```text
http://127.0.0.1:7071/runtime/webhooks/mcp
```

## Azure Deployment Setup

This repo includes a Bicep template at `infra/main.bicep` that provisions the infrastructure required for this Function App on Flex Consumption.

## Deploy Infrastructure with Bicep

1. Sign in and select your subscription.

```powershell
az login
az account set --subscription <subscription-id-or-name>
```

2. Create (or reuse) a resource group.

```powershell
az group create --name <resource-group-name> --location <azure-region>
```

3. Deploy the Bicep template.

```powershell
az deployment group create --name weather-func-infra --resource-group <resource-group-name> --template-file infra/main.bicep --parameters environmentName=dev location=<azure-region>
```

4. Capture outputs (especially the generated Function App name).

```powershell
az deployment group show --resource-group <resource-group-name> --name weather-func-infra --query properties.outputs -o json
az deployment group show --resource-group <resource-group-name> --name weather-func-infra --query properties.outputs.functionAppResourceName.value -o tsv
```

Optional: provide custom names and runtime parameters during deployment.

```powershell
az deployment group create --name weather-func-infra --resource-group <resource-group-name> --template-file infra/main.bicep --parameters environmentName=dev location=<azure-region> functionAppName=<custom-function-app-name> functionPlanName=<custom-plan-name> storageAccountName=<custom-storage-account-name> functionAppRuntime=python functionAppRuntimeVersion=3.12 maximumInstanceCount=100 instanceMemoryMB=2048
```

The template creates:

- Log Analytics workspace
- Application Insights (workspace-based)
- Storage account with deployment container
- Flex Consumption plan (`FC1`)
- Linux Function App (`functionapp,linux`) with managed identity and Function App config
- Required RBAC assignments for managed identity-based storage and telemetry

Tip: if you omit `functionAppName`, the template auto-generates one and returns it as `functionAppResourceName` output.

## Deploy the Function App Code

Use the Function App name from Bicep output `functionAppResourceName` (or your custom `functionAppName` if you set one).

1. Sign in to Azure.

```powershell
az login
```

2. Set the required app setting on the target Function App.

```powershell
az functionapp config appsettings set --name <function-app-name> --resource-group <resource-group-name> --settings PYTHON_ISOLATE_WORKER_DEPENDENCIES=1 WEATHER_HTTP_TIMEOUT_SECONDS=10
```

3. Publish from this project directory.

```powershell
func azure functionapp publish <function-app-name> --python
```

This deploys the files in this folder to the target Function App.

## Verify MCP Endpoint Access

The Azure Functions MCP extension requires the system key named `mcp_extension` unless you explicitly configure anonymous webhook access.

Get the key with Azure CLI:

```powershell
az functionapp keys list --name <function-app-name> --resource-group <resource-group-name> --query "systemKeys.mcp_extension" -o tsv
```

Your remote MCP endpoint is:

```text
https://<function-app-name>.azurewebsites.net/runtime/webhooks/mcp
```

## Local Client Setup

1. Use the same virtual environment or create a separate one.

2. Install client dependencies.

```powershell
pip install -r client/requirements.txt
```

Alternative from repo root (compatibility file):

```powershell
pip install -r requirements-client.txt
```

3. Create `.env` from the example.

```powershell
Copy-Item client/.env.example client/.env
```

4. Fill in these values in `client/.env`:

- `PROJECT_ENDPOINT`
- `MODEL_DEPLOYMENT_NAME`
- `FUNCTION_APP_NAME`
- `MCP_EXTENSION_KEY`

## Run the Remote Client

With `.env` populated:

```powershell
python client/remote_client.py
```

Or override values explicitly:

```powershell
python client/remote_client.py --function-app-name <function-app-name> --mcp-key <mcp-extension-key> --project-endpoint <foundry-project-endpoint> --model-deployment <model-deployment-name>
```

## How the Client Works

- Connects to the deployed MCP server over Streamable HTTP.
- Discovers the remote MCP tools from the Function App.
- Creates an Azure AI Foundry agent with those tool definitions.
- Lets the model decide when to call the MCP tools.
- Sends tool results back to the model and prints the final response.

## Notes

- `host.json` uses the stable extension bundle range (`[4.*, 5.0.0)`) and enables Application Insights sampling.
- `local.settings.json.example` includes `PYTHON_ISOLATE_WORKER_DEPENDENCIES=1` because the MCP decorators require it.
- `local.settings.json.example` includes `WEATHER_HTTP_TIMEOUT_SECONDS` so API timeout tuning is explicit.
- `client/remote_client.py` loads `.env` from `client/.env` and also supports a fallback root `.env`.
- The deployed server may expose tools without exposing prompts or resources. The client handles that.

## Deployment Hygiene

- `.funcignore` excludes local-only client files and secrets so publish payloads remain minimal.
- Keep one function app per workload to simplify scaling and blast-radius management.
- Prefer key-based or stronger auth for the MCP endpoint; avoid anonymous access.

## Useful Commands

Run tests:

```powershell
python -m pytest tests
```

Tail logs:

```powershell
az functionapp log tail --name <function-app-name> --resource-group <resource-group-name>
```

Re-deploy code:

```powershell
func azure functionapp publish <function-app-name> --python
```

## Security

- Do not commit `.env`.
- Do not commit `local.settings.json`.
- Treat `MCP_EXTENSION_KEY` as a secret.
