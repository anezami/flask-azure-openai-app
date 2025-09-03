import os
import sys
from pathlib import Path


def main():
    # Ensure project root is on sys.path when running from scripts/
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    # Ensure local auth is disabled for tests
    os.environ.setdefault("DISABLE_AUTH", "true")
    os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

    try:
        from app import create_app
    except Exception as e:
        print(f"Import error: {e}")
        sys.exit(2)

    app = create_app()
    client = app.test_client()

    # 1) Health
    resp = client.get("/health")
    assert resp.status_code == 200, f"/health expected 200, got {resp.status_code}"
    data = resp.get_json()
    assert data and data.get("status") == "ok", f"/health payload unexpected: {data}"
    print("/health: OK")

    # 2) Home page
    resp = client.get("/")
    assert resp.status_code == 200, f"/ expected 200, got {resp.status_code}"
    assert (b"Text Assistant" in resp.data) or (b"Text-Assistent" in resp.data), \
        "Home page did not contain expected heading"
    print("/: OK")

    # 3) Preview (no model call)
    resp = client.post("/preview", data={
        "text": "Hello world",
        "translate_mode": "off"
    })
    assert resp.status_code == 200, f"/preview expected 200, got {resp.status_code}"
    assert b"Preview" in resp.data or b"Vorschau" in resp.data, \
        "Preview page did not contain expected content"
    print("/preview: OK")

    print("Smoke tests passed.")


if __name__ == "__main__":
    main()
