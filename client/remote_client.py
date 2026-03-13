import argparse
import asyncio
import json
import os
from contextlib import AsyncExitStack
from pathlib import Path

import httpx
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FunctionTool, PromptAgentDefinition
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from openai import BadRequestError
from openai.types.responses.response_input_param import (
    FunctionCallOutput,
    ResponseInputParam,
)

CURRENT_DIR = Path(__file__).resolve().parent
load_dotenv(CURRENT_DIR / ".env")
load_dotenv(CURRENT_DIR.parent / ".env")


def _default_server_url(function_app_name: str) -> str:
    return f"https://{function_app_name}.azurewebsites.net/runtime/webhooks/mcp"


def _resolve_settings(args: argparse.Namespace) -> tuple[str, dict[str, str]]:
    server_url = args.server_url or os.getenv("MCP_SERVER_URL")
    function_app_name = args.function_app_name or os.getenv("FUNCTION_APP_NAME")
    mcp_key = args.mcp_key or os.getenv("MCP_EXTENSION_KEY")

    if not server_url:
        if not function_app_name:
            raise ValueError(
                "Provide --server-url or set MCP_SERVER_URL, or provide --function-app-name/FUNCTION_APP_NAME."
            )
        server_url = _default_server_url(function_app_name)

    headers: dict[str, str] = {}
    if mcp_key:
        headers["x-functions-key"] = mcp_key

    return server_url, headers


def _resolve_agent_settings(args: argparse.Namespace) -> tuple[str, str]:
    project_endpoint = args.project_endpoint or os.getenv("PROJECT_ENDPOINT")
    model_deployment = args.model_deployment or os.getenv("MODEL_DEPLOYMENT_NAME")

    if not project_endpoint or not model_deployment:
        raise ValueError(
            "Set PROJECT_ENDPOINT and MODEL_DEPLOYMENT_NAME in .env, or pass --project-endpoint and --model-deployment."
        )

    return project_endpoint, model_deployment


def _tool_parameters_schema(tool) -> dict:
    schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
    if isinstance(schema, dict):
        return schema

    return {
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    }


def _tool_output_text(result) -> str:
    if not getattr(result, "content", None):
        return ""

    chunks: list[str] = []
    for item in result.content:
        text = getattr(item, "text", None)
        if text is not None:
            chunks.append(str(text))

    return "\n".join(chunks)


def _extract_response_text(resp) -> str:
    if getattr(resp, "output_text", None):
        return resp.output_text

    texts: list[str] = []
    for item in getattr(resp, "output", []) or []:
        if getattr(item, "type", None) != "message":
            continue
        for content_part in getattr(item, "content", []) or []:
            text_value = getattr(content_part, "text", None)
            if text_value:
                texts.append(text_value)

    return "\n".join(texts).strip()


def _get_function_calls(resp) -> list[dict]:
    calls: list[dict] = []
    for item in getattr(resp, "output", []) or []:
        if getattr(item, "type", None) != "function_call":
            continue

        raw_arguments = getattr(item, "arguments", "{}")
        if isinstance(raw_arguments, dict):
            kwargs = raw_arguments
        else:
            try:
                kwargs = json.loads(raw_arguments or "{}")
            except (TypeError, json.JSONDecodeError):
                kwargs = {}

        call_id = (
            getattr(item, "call_id", None)
            or getattr(item, "callId", None)
            or getattr(item, "id", None)
        )

        calls.append(
            {
                "call_id": call_id,
                "function_name": getattr(item, "name", ""),
                "kwargs": kwargs,
            }
        )

    return calls


async def _safe_list_resources(session: ClientSession) -> list:
    try:
        return (await session.list_resources()).resources
    except McpError as ex:
        if "resources/list" in str(ex):
            return []
        raise


async def _safe_list_prompts(session: ClientSession) -> list:
    try:
        return (await session.list_prompts()).prompts
    except McpError as ex:
        if "prompts/list" in str(ex):
            return []
        raise


async def connect_to_server(
    exit_stack: AsyncExitStack, server_url: str, headers: dict[str, str]
) -> ClientSession:
    http_client = httpx.AsyncClient(headers=headers if headers else None, timeout=30.0)
    await exit_stack.enter_async_context(http_client)

    read_stream, write_stream, _get_session_id = await exit_stack.enter_async_context(
        streamable_http_client(server_url, http_client=http_client)
    )
    session = await exit_stack.enter_async_context(
        ClientSession(read_stream, write_stream)
    )
    await session.initialize()

    tools = (await session.list_tools()).tools
    print(f"Connected to {server_url}")
    print("Remote MCP tools:", ", ".join(tool.name for tool in tools))

    resources = await _safe_list_resources(session)
    if resources:
        print("Resources:", ", ".join(str(resource.uri) for resource in resources))

    prompts = await _safe_list_prompts(session)
    if prompts:
        print("Prompts:", ", ".join(prompt.name for prompt in prompts))

    return session


async def chat_loop(
    session: ClientSession, project_endpoint: str, model_deployment: str
) -> None:
    with (
        DefaultAzureCredential() as credential,
        AIProjectClient(
            endpoint=project_endpoint, credential=credential
        ) as project_client,
        project_client.get_openai_client() as openai_client,
    ):
        tools = (await session.list_tools()).tools

        def make_tool_func(tool_name):
            async def tool_func(**kwargs):
                return await session.call_tool(tool_name, kwargs)

            tool_func.__name__ = tool_name
            return tool_func

        functions_dict = {tool.name: make_tool_func(tool.name) for tool in tools}

        mcp_function_tools: list[FunctionTool] = []
        for tool in tools:
            mcp_function_tools.append(
                FunctionTool(
                    name=tool.name,
                    description=tool.description,
                    parameters=_tool_parameters_schema(tool),
                    strict=False,
                )
            )

        resource_text = ""
        for resource in await _safe_list_resources(session):
            content = await session.read_resource(resource.uri)
            resource_text += f"\n\nResource: {resource.uri}\n"
            resource_text += content.contents[0].text

        instructions = (
            "You are a weather assistant. Use the remote MCP weather tools to answer questions "
            "about current weather, forecasts, weather code descriptions, and cross-city comparisons. "
            "When data is unavailable, explain clearly what failed and why."
        )
        if resource_text:
            instructions = f"{instructions}\n\n{resource_text}"

        agent = project_client.agents.create_version(
            agent_name="remote-weather-agent",
            definition=PromptAgentDefinition(
                model=model_deployment,
                instructions=instructions,
                tools=mcp_function_tools,
            ),
        )

        conversation = openai_client.conversations.create()

        print("\nRemote weather agent ready. Type questions or 'quit' to exit.")

        try:
            while True:
                user_input = input("USER: ").strip()
                if user_input.lower() == "quit":
                    print("Exiting chat.")
                    break

                openai_client.conversations.items.create(
                    conversation_id=conversation.id,
                    items=[{"type": "message", "role": "user", "content": user_input}],
                )

                try:
                    response = openai_client.responses.create(
                        conversation=conversation.id,
                        extra_body={
                            "agent": {"name": agent.name, "type": "agent_reference"}
                        },
                    )
                except BadRequestError as ex:
                    error_text = str(ex)
                    if "No tool output found for function call" in error_text:
                        conversation = openai_client.conversations.create()
                        openai_client.conversations.items.create(
                            conversation_id=conversation.id,
                            items=[
                                {
                                    "type": "message",
                                    "role": "user",
                                    "content": user_input,
                                }
                            ],
                        )
                        response = openai_client.responses.create(
                            conversation=conversation.id,
                            extra_body={
                                "agent": {"name": agent.name, "type": "agent_reference"}
                            },
                        )
                    else:
                        raise

                if response.status == "failed":
                    print(f"Response failed: {response.error}")
                    continue

                max_tool_rounds = 8
                for _ in range(max_tool_rounds):
                    input_list: ResponseInputParam = []
                    function_calls = _get_function_calls(response)

                    for call in function_calls:
                        function_name = call["function_name"]
                        kwargs = call["kwargs"]
                        call_id = call["call_id"]
                        required_function = functions_dict.get(function_name)

                        if required_function is None:
                            tool_text = f"Error: no MCP function registered for '{function_name}'"
                        else:
                            try:
                                output = await required_function(**kwargs)
                                tool_text = _tool_output_text(output)
                            except Exception as ex:
                                tool_text = (
                                    f"Tool call failed for '{function_name}': {ex}"
                                )

                        if call_id:
                            input_list.append(
                                FunctionCallOutput(
                                    type="function_call_output",
                                    call_id=call_id,
                                    output=tool_text,
                                )
                            )

                    if function_calls and not input_list:
                        print(
                            "Response contained function calls but none had usable call IDs; resetting conversation."
                        )
                        conversation = openai_client.conversations.create()
                        break

                    if not input_list:
                        break

                    response = openai_client.responses.create(
                        input=input_list,
                        previous_response_id=response.id,
                        extra_body={
                            "agent": {"name": agent.name, "type": "agent_reference"}
                        },
                    )

                    if response.status == "failed":
                        print(f"Response failed after tool call: {response.error}")
                        break

                output_text = _extract_response_text(response)
                if output_text:
                    print(f"AGENT: {output_text}")
                else:
                    print("AGENT: <no text response>")
        finally:
            project_client.agents.delete_version(
                agent_name=agent.name,
                agent_version=agent.version,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remote MCP client for Azure Functions weather app"
    )
    parser.add_argument(
        "--server-url",
        help="Full MCP endpoint URL, e.g. https://<app>.azurewebsites.net/runtime/webhooks/mcp",
    )
    parser.add_argument(
        "--function-app-name",
        help="Function App name used to construct the endpoint URL",
    )
    parser.add_argument("--mcp-key", help="Functions MCP extension system key")
    parser.add_argument(
        "--project-endpoint",
        help="Azure AI Foundry project endpoint. Defaults to PROJECT_ENDPOINT from .env",
    )
    parser.add_argument(
        "--model-deployment",
        help="Azure AI Foundry model deployment name. Defaults to MODEL_DEPLOYMENT_NAME from .env",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        server_url, headers = _resolve_settings(args)
        project_endpoint, model_deployment = _resolve_agent_settings(args)
    except ValueError as ex:
        print(f"Configuration error: {ex}")
        return

    try:

        async def runner() -> None:
            exit_stack = AsyncExitStack()
            try:
                session = await connect_to_server(exit_stack, server_url, headers)
                await chat_loop(session, project_endpoint, model_deployment)
            finally:
                await exit_stack.aclose()

        asyncio.run(runner())
    except KeyboardInterrupt:
        print("\nInterrupted")


if __name__ == "__main__":
    main()
