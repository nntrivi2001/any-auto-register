"""Qwen (chat.qwen.ai) registration protocol via Playwright.

Qwen web registration flow (verified via debugging):
    1. Open https://chat.qwen.ai/auth?mode=register
    2. Fill Full Name + Email + Password + Confirm Password
    3. Accept Terms checkbox
    4. Submit form
    5. JWT token issued immediately in "token" cookie
    6. Page shows "pending activation" — email verification is deferred
    7. Activation email sent to user with link:
       https://chat.qwen.ai/api/v1/auths/activate?id=UUID&token=HASH
    8. Calling activation URL activates the account

Note: No OTP, no captcha. Token available immediately; activation is
deferred and can be done by visiting the activation URL from email.
"""

import json
import random
import re
import string
import time
from typing import Callable, Optional
from urllib.parse import urlparse, parse_qs

import requests
from playwright.sync_api import Page

QWEN_AUTH_URL = "https://chat.qwen.ai/auth"
QWEN_ACTIVATE_URL = "https://chat.qwen.ai/api/v1/auths/activate"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def _rand_password(n: int = 16) -> str:
    """Generate password with at least one upper, lower, digit, special."""
    chars = string.ascii_letters + string.digits + "!@#$"
    pw = [
        random.choice(string.ascii_uppercase),
        random.choice(string.ascii_lowercase),
        random.choice(string.digits),
        random.choice("!@#$"),
    ]
    pw += [random.choice(chars) for _ in range(n - 4)]
    random.shuffle(pw)
    return "".join(pw)


def extract_activation_link(text: str) -> str | None:
    """Extract Qwen activation URL from email text."""
    if not text:
        return None
    urls = re.findall(r'https?://[^\s"\'<>\]]+', text)
    for u in urls:
        if "activate" in u.lower() and "qwen" in u.lower():
            return u
    # Also check markdown-style links
    md_links = re.findall(r'\(([^)]*activate[^)]*)\)', text)
    for u in md_links:
        if "qwen" in u.lower():
            return u.strip()
    return None


def call_activation_api(activation_url: str, user_agent: str = UA) -> dict:
    """Call the Qwen activation API directly."""
    parsed = urlparse(activation_url)
    params = parse_qs(parsed.query)
    act_id = params.get("id", [None])[0]
    act_token = params.get("token", [None])[0]

    if not act_id or not act_token:
        return {"ok": False, "error": "Missing id/token params"}

    url = f"{QWEN_ACTIVATE_URL}?id={act_id}&token={act_token}"
    try:
        r = requests.get(
            url,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=15,
            allow_redirects=True,
        )
        return {
            "ok": r.status_code in (200, 302),
            "status_code": r.status_code,
            "final_url": r.url,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def wait_for_activation_link(
    mailbox,
    mail_acct,
    timeout: int = 120,
    before_ids: set = None,
) -> str | None:
    """Poll mailbox for Qwen activation email and extract link."""
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(5)
        try:
            messages = mailbox.get_messages(mail_acct, before_ids=before_ids)
            for msg in messages:
                body = mailbox.get_message_body(mail_acct, msg.get("id")) or ""
                link = extract_activation_link(body)
                if link:
                    return link
        except Exception:
            continue
    return None


class QwenRegister:
    """Automate Qwen account registration via Playwright."""

    def __init__(self, executor, log_fn: Callable = print):
        """executor must be a PlaywrightExecutor (headless or headed)."""
        self.executor = executor
        self.log = log_fn
        self._max_retries = 2

    def register(
        self,
        email: str,
        password: str = None,
        full_name: str = "",
        _otp_callback: Optional[Callable] = None,
        _captcha_token: str = "",
    ) -> dict:
        if not password:
            password = _rand_password()

        if not full_name:
            full_name = email.split("@")[0].replace(".", " ").replace("_", " ").title()

        self.log(f"Qwen registration — email: {email}, name: {full_name}")

        page = self.executor.page
        if page is None:
            raise RuntimeError(
                "Qwen requires a browser executor (headless/headed Playwright). "
                "Please select 'headless' or 'headed' executor."
            )

        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                self.log(f"  Retry {attempt}/{self._max_retries}...")
                time.sleep(5)

            result = self._try_register(page, email, password, full_name)
            if result.get("token"):
                return result

            if attempt < self._max_retries:
                self.log(f"  No token, retrying...")
            else:
                self.log(f"  WARNING: No auth token after {self._max_retries + 1} attempts")

        return {"email": email, "password": password, "full_name": full_name, "tokens": {}, "status": "failed"}

    def _try_register(self, page: Page, email: str, password: str, full_name: str) -> dict:
        """One attempt at registration. Returns dict with tokens."""
        try:
            # Step 1: navigate
            page.goto(f"{QWEN_AUTH_URL}?mode=register", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1000)

            # Step 2: fill full name
            name_input = self._find_input(
                page,
                selectors=[
                    'input[name="username"]',
                    'input[placeholder*="name" i]',
                    'input[placeholder*="Name" i]',
                ],
            )
            name_input.click()
            page.wait_for_timeout(100)
            name_input.fill(full_name)
            page.wait_for_timeout(100)

            # Step 3: fill email
            email_input = self._find_input(
                page,
                selectors=[
                    'input[type="email"]',
                    'input[autocomplete="email"]',
                    'input[name="email"]',
                ],
            )
            email_input.click()
            page.wait_for_timeout(100)
            email_input.fill(email)
            page.wait_for_timeout(100)

            # Step 4: fill password
            pw_input = self._find_input(
                page,
                selectors=[
                    'input[name="password"]',
                    'input[placeholder*="password" i]',
                ],
            )
            pw_input.click()
            page.wait_for_timeout(100)
            pw_input.fill(password)
            page.wait_for_timeout(100)

            # Step 5: fill confirm password
            cpw_input = self._find_input(
                page,
                selectors=[
                    'input[name="checkPassword"]',
                    'input[name="confirmPassword"]',
                    'input[name="confirm_password"]',
                ],
            )
            cpw_input.click()
            page.wait_for_timeout(100)
            cpw_input.fill(password)
            page.wait_for_timeout(100)

            # Step 6: accept terms
            page.wait_for_timeout(300)
            checkbox = page.query_selector('input[type="checkbox"]')
            if checkbox and checkbox.is_visible():
                checkbox.click()
                page.wait_for_timeout(200)

            # Step 7: submit
            submit_btn = self._find_submit(page)
            submit_btn.click()
            page.wait_for_timeout(4000)

            # Step 8: extract JWT token from "token" cookie
            tokens = self._extract_tokens(page)
            self.log(f"  Current URL: {page.url}")
            self.log(f"  Tokens found: {list(tokens.keys()) if tokens else 'none'}")

            if tokens.get("token") or tokens.get("cookie:token"):
                return {
                    "email": email,
                    "password": password,
                    "full_name": full_name,
                    "tokens": tokens,
                    "status": "success",
                }

            return {"tokens": {}}

        except Exception as e:
            self.log(f"  Error: {e}")
            return {"tokens": {}}

    # ---- helpers ----

    @staticmethod
    def _find_input(page, selectors: list):
        """Try multiple selectors until one returns a visible input."""
        for sel in selectors:
            try:
                el = page.wait_for_selector(sel, timeout=5000)
                if el and el.is_visible():
                    return el
            except Exception:
                continue
        el = page.wait_for_selector("input", timeout=10000)
        if el and el.is_visible():
            return el
        raise RuntimeError("Could not find any visible input field on the page")

    @staticmethod
    def _find_submit(page):
        """Find and return submit/continue button."""
        selectors = [
            'button[type="submit"]',
            'button:has-text("Create Account")',
            'button:has-text("Register")',
            'button:has-text("Sign up")',
            'button:has-text("Continue")',
            'button:has-text("Next")',
            'button:has-text("注册")',
        ]
        for sel in selectors:
            try:
                el = page.wait_for_selector(sel, timeout=5000)
                if el and el.is_visible():
                    return el
            except Exception:
                continue
        raise RuntimeError("Could not find submit button")

    @staticmethod
    def _extract_tokens(page) -> dict:
        """Extract auth tokens from localStorage, sessionStorage, and cookies."""
        tokens = {}

        try:
            storage = page.evaluate("() => JSON.stringify(localStorage)")
            data = json.loads(storage)
            for key, value in data.items():
                kl = key.lower()
                if any(kw in kl for kw in ["token", "auth", "credential", "session", "access", "refresh"]):
                    if value and len(value) > 10:
                        tokens[key] = value
        except Exception:
            pass

        try:
            session = page.evaluate("() => JSON.stringify(sessionStorage)")
            data = json.loads(session)
            for key, value in data.items():
                kl = key.lower()
                if any(kw in kl for kw in ["token", "auth", "credential"]):
                    if value and len(value) > 10:
                        tokens[f"session:{key}"] = value
        except Exception:
            pass

        try:
            for cookie in page.context.cookies():
                cl = cookie["name"].lower()
                if any(kw in cl for kw in ["token", "auth", "session"]):
                    tokens[f'cookie:{cookie["name"]}'] = cookie["value"]
        except Exception:
            pass

        return tokens
