#!/usr/bin/env python3
"""
Qwen OAuth Connect for 9router

Authenticates with Qwen via Device Code Flow + PKCE and saves the tokens
to your local 9router instance so 9router can route requests through it.

Based on 9router's Qwen OAuth implementation:
  - Client ID: f0304373b74a44d2b584a3fb70ca9e56
  - Device Code URL: https://chat.qwen.ai/api/v1/oauth2/device/code
  - Token URL: https://chat.qwen.ai/api/v1/oauth2/token
  - Scope: openid profile email model.completion

Usage:
    pip install httpx
    python tools/qwen_9router_oauth.py --nine-router-url http://localhost:3000

    # Or without 9router (just get tokens):
    python tools/qwen_9router_oauth.py
"""

import argparse
import hashlib
import base64
import json
import os
import sys
import time
import secrets

try:
    import httpx
except ImportError:
    print("Error: httpx not installed. Run: pip install httpx")
    sys.exit(1)


# ─── Qwen OAuth constants (from 9router src/lib/oauth/constants/oauth.js) ───

QWEN_CLIENT_ID = "f0304373b74a44d2b584a3fb70ca9e56"
QWEN_DEVICE_CODE_URL = "https://chat.qwen.ai/api/v1/oauth2/device/code"
QWEN_TOKEN_URL = "https://chat.qwen.ai/api/v1/oauth2/token"
QWEN_SCOPE = "openid profile email model.completion"


def generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    return code_verifier, code_challenge


def request_device_code(code_challenge: str) -> dict:
    """Request device authorization code from Qwen."""
    resp = httpx.post(
        QWEN_DEVICE_CODE_URL,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        data={
            "client_id": QWEN_CLIENT_ID,
            "scope": QWEN_SCOPE,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def poll_for_token(
    device_code: str,
    code_verifier: str,
    max_attempts: int = 60,
    interval: int = 5,
) -> dict:
    """Poll Qwen token endpoint until user authorizes or timeout."""
    poll_interval = interval

    for attempt in range(max_attempts):
        time.sleep(poll_interval)

        resp = httpx.post(
            QWEN_TOKEN_URL,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": QWEN_CLIENT_ID,
                "device_code": device_code,
                "code_verifier": code_verifier,
            },
            timeout=30,
        )

        if resp.status_code == 200:
            return resp.json()

        error_data = resp.json()
        error = error_data.get("error", "")

        if error == "authorization_pending":
            remaining = max_attempts - attempt - 1
            print(f"  ⏳  Waiting for authorization... ({remaining} attempts left)")
            continue
        elif error == "slow_down":
            print("  ⏳  Server asked to slow down, increasing interval...")
            poll_interval += 5
            continue
        elif error == "expired_token":
            raise RuntimeError("Device code expired. Please try again.")
        elif error == "access_denied":
            raise RuntimeError("Access denied by user.")
        else:
            raise RuntimeError(
                f"Token error: {error} — {error_data.get('error_description', '')}"
            )

    raise RuntimeError("Authorization timed out after 5 minutes.")


def save_to_9router(
    tokens: dict,
    nine_router_url: str,
    nine_router_token: str,
    nine_router_user_id: str,
) -> dict:
    """Save Qwen tokens to 9router via its API."""
    resp = httpx.post(
        f"{nine_router_url}/api/cli/providers/qwen",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {nine_router_token}",
            "X-User-Id": nine_router_user_id,
        },
        json={
            "accessToken": tokens["access_token"],
            "refreshToken": tokens["refresh_token"],
            "expiresIn": tokens.get("expires_in"),
            "resourceUrl": tokens.get("resource_url", ""),
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def connect_qwen(
    nine_router_url: str = None,
    nine_router_token: str = None,
    nine_router_user_id: str = None,
) -> dict:
    """Complete Qwen OAuth Device Code Flow and optionally save to 9router."""
    print("=" * 60)
    print("Qwen OAuth Connect for 9router")
    print("=" * 60)

    # Step 1: Generate PKCE
    print("\n[1/3] Generating PKCE...")
    code_verifier, code_challenge = generate_pkce()

    # Step 2: Request device code
    print("[2/3] Requesting device authorization...")
    device_data = request_device_code(code_challenge)

    print(f"\n  📋 Please visit the following URL and authorize:\n")
    print(f"     {device_data.get('verification_uri', 'https://chat.qwen.ai')}\n")
    print(f"     User Code: {device_data.get('user_code', 'N/A')}\n")

    if device_data.get("verification_uri_complete"):
        print(f"  🔗 Direct link (opens browser):")
        print(f"     {device_data['verification_uri_complete']}\n")

        # Try to open browser
        try:
            import webbrowser
            webbrowser.open(device_data["verification_uri_complete"])
            print("  ✅ Browser opened automatically.\n")
        except Exception:
            print("  ⚠️  Could not open browser automatically. Please open the link manually.\n")

    # Step 3: Poll for token
    print("[3/3] Waiting for you to authorize...\n")
    interval = device_data.get("interval", 5)
    tokens = poll_for_token(device_data["device_code"], code_verifier, interval=interval)

    print("\n  ✅ Authorization successful!")
    print(f"\n  Access Token:  {tokens['access_token'][:30]}...")
    print(f"  Refresh Token: {tokens.get('refresh_token', 'N/A')[:30] if tokens.get('refresh_token') else 'N/A'}...")
    print(f"  Expires In:    {tokens.get('expires_in', 'N/A')} seconds")
    if tokens.get("resource_url"):
        print(f"  Resource URL:  {tokens['resource_url']}")

    # Save to 9router if URL provided
    if nine_router_url:
        if not nine_router_token:
            nine_router_token = input("\n  9router token (or leave empty to skip): ").strip()

        if not nine_router_user_id:
            nine_router_user_id = input("  9router user ID: ").strip()

        if nine_router_token and nine_router_user_id:
            print(f"\n  Saving tokens to 9router at {nine_router_url}...")
            result = save_to_9router(tokens, nine_router_url, nine_router_token, nine_router_user_id)
            print(f"  ✅ Tokens saved to 9router: {result}")
        else:
            print("\n  ⚠️  No 9router credentials provided. Tokens not saved to 9router.")

    # Also save locally for reference
    local_save_path = os.path.expanduser("~/.9router/qwen_oauth.json")
    os.makedirs(os.path.dirname(local_save_path), exist_ok=True)
    local_data = {
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token", ""),
        "expires_in": tokens.get("expires_in", 0),
        "resource_url": tokens.get("resource_url", ""),
        "expires_at": int(time.time()) + tokens.get("expires_in", 0),
    }
    with open(local_save_path, "w") as f:
        json.dump(local_data, f, indent=2)
    print(f"\n  💾 Tokens saved locally to: {local_save_path}")

    return tokens


def main():
    parser = argparse.ArgumentParser(description="Qwen OAuth Connect for 9router")
    parser.add_argument("--nine-router-url", default=None, help="9router server URL (e.g. http://localhost:3000)")
    parser.add_argument("--nine-router-token", default=None, help="9router auth token")
    parser.add_argument("--nine-router-user-id", default=None, help="9router user ID")
    args = parser.parse_args()

    try:
        tokens = connect_qwen(
            nine_router_url=args.nine_router_url,
            nine_router_token=args.nine_router_token,
            nine_router_user_id=args.nine_router_user_id,
        )
        print("\n" + "=" * 60)
        print("Qwen connected successfully!")
        print("=" * 60)
    except KeyboardInterrupt:
        print("\n\n  ❌ Cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n  ❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
