import os
from typing import Optional

import httpx
from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

# Notes:
# - This module wraps Azure OpenAI Chat Completions calls using the 'openai' SDK v1+ with Azure endpoints.
# - Environment variables required:
#   AZURE_OPENAI_ENDPOINT: e.g., https://<your-ai-foundry-endpoint>.openai.azure.com/
#   AZURE_OPENAI_DEPLOYMENT: the deployment name for gpt-4o
#   AZURE_OPENAI_API_VERSION: e.g., 2024-06-01

_client: Optional[AzureOpenAI] = None


def get_client() -> AzureOpenAI:
    global _client
    if _client is None:
        endpoint = os.getenv('AZURE_OPENAI_ENDPOINT')
        api_version = os.getenv('AZURE_OPENAI_API_VERSION', '2024-06-01')
        if not endpoint:
            raise RuntimeError('Missing AZURE_OPENAI_ENDPOINT')
        # Managed Identity token provider
        credential = DefaultAzureCredential()
        token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
        timeout = float(os.getenv('AOAI_HTTP_TIMEOUT', '60'))
        http_client = httpx.Client(timeout=timeout)
        _client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_version=api_version,
            azure_ad_token_provider=token_provider,
            http_client=http_client,
        )
    return _client


def call_chat_completion(system_prompt: str,
                         user_content: str,
                         deployment_name: Optional[str] = None,
                         temperature: float = 0.2,
                         max_output_tokens: int = 2048) -> str:
    """
    Call Azure OpenAI Chat Completions API and return the text content.
    """
    client = get_client()
    deployment = deployment_name or os.getenv('AZURE_OPENAI_DEPLOYMENT')
    if not deployment:
        raise RuntimeError('Missing deployment name. Set AZURE_OPENAI_DEPLOYMENT env var.')

    # Compose messages
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content}
    ]

    resp = client.chat.completions.create(
        model=deployment,
        messages=messages,
        temperature=temperature,
        max_tokens=max_output_tokens,
    )

    if not resp.choices:
        raise RuntimeError('No choices returned from Azure OpenAI response.')

    return resp.choices[0].message.content or ""
