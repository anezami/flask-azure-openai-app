import os
import sys
from pathlib import Path


def main():
    # Load env for local development if available
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    # Ensure project root on sys.path
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    # Ensure required env vars are present
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-06-01")
    if not endpoint or not deployment:
        print("Missing AZURE_OPENAI_ENDPOINT or AZURE_OPENAI_DEPLOYMENT in environment.")
        sys.exit(2)

    # Keep the call tiny and deterministic
    system_prompt = "You are a concise assistant. Answer with a single word."
    user_content = "Reply with OK"

    try:
        from azure_openai_client import call_chat_completion
        out = call_chat_completion(
            system_prompt=system_prompt,
            user_content=user_content,
            deployment_name=deployment,
            temperature=0.0,
            max_output_tokens=8,
        )
        print("Azure OpenAI call succeeded. Response:\n" + (out or "<empty>"))
        sys.exit(0)
    except Exception as e:
        print(f"Azure OpenAI call failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
