"""Qwen (chat.qwen.ai) registration protocol via Playwright.

Qwen web registration flow:
    1. Open https://chat.qwen.ai/auth?mode=register
    2. Enter email + password
    3. Solve Turnstile captcha
    4. Submit form
    5. Receive OTP via email
    6. Enter OTP to verify
    7. Registration complete, tokens stored in localStorage
"""

import json
import random
import string
from typing import Callable, Optional

QWEN_AUTH_URL = "https://chat.qwen.ai/auth"

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


class QwenRegister:
    """Automate Qwen account registration via Playwright."""

    def __init__(self, executor, log_fn: Callable = print):
        """executor must be a PlaywrightExecutor (headless or headed)."""
        self.executor = executor
        self.log = log_fn

    def register(
        self,
        email: str,
        password: str = None,
        otp_callback: Optional[Callable] = None,
        captcha_token: str = "",
    ) -> dict:
        if not password:
            password = _rand_password()

        self.log(f"Qwen registration — email: {email}")

        page = self.executor.page
        if page is None:
            raise RuntimeError(
                "Qwen requires a browser executor (headless/headed Playwright). "
                "Please select 'headless' or 'headed' executor."
            )

        # Step 1: navigate to registration
        self.log("Step 1: Opening registration page...")
        page.goto(f"{QWEN_AUTH_URL}?mode=register", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # Step 2: fill email
        self.log("Step 2: Entering email...")
        email_input = self._find_input(
            page,
            selectors=[
                'input[type="email"]',
                'input[autocomplete="email"]',
                'input[name="email"]',
                'input[placeholder*="mail" i]',
                'input[placeholder*="邮箱" i]',
            ],
        )
        email_input.fill(email)
        page.wait_for_timeout(500)

        # Step 3: fill password
        self.log("Step 3: Entering password...")
        pw_input = self._find_input(
            page,
            selectors=[
                'input[type="password"]',
                'input[name="password"]',
                'input[placeholder*="password" i]',
                'input[placeholder*="密码" i]',
            ],
        )
        pw_input.fill(password)
        page.wait_for_timeout(500)

        # Step 4: handle Turnstile captcha if present
        self.log("Step 4: Checking for Turnstile captcha...")
        page.wait_for_timeout(2000)
        if captcha_token:
            # Inject pre-solved Turnstile token
            page.evaluate(f"""() => {{
                window.turnstileCallback = function() {{ return '{captcha_token}'; }};
            }}""")
            self.log("  Turnstile token injected")

        # Step 5: submit
        self.log("Step 5: Submitting registration...")
        submit_btn = self._find_submit(page)
        submit_btn.click()
        page.wait_for_timeout(3000)

        # Step 6: OTP verification (if required)
        self.log("Step 6: Checking for OTP verification...")
        otp_input = self._wait_for_otp_input(page)
        if otp_input:
            self.log("  OTP input found, waiting for code...")
            if otp_callback:
                code = otp_callback()
            else:
                code = input("  Enter OTP code: ")

            if not code:
                raise RuntimeError("No OTP code provided")

            self.log(f"  Entering OTP...")
            otp_input.fill(code)
            page.wait_for_timeout(1000)

            # Submit OTP
            otp_submit = self._find_submit(page)
            if otp_submit:
                otp_submit.click()
            else:
                otp_input.press("Enter")

            page.wait_for_timeout(5000)

        # Step 7: extract tokens from localStorage
        self.log("Step 7: Extracting tokens...")
        page.wait_for_timeout(2000)
        tokens = self._extract_tokens(page)

        self.log(f"  Current URL: {page.url}")
        self.log(f"  Tokens found: {list(tokens.keys()) if tokens else 'none'}")

        if not tokens.get("access_token") and not tokens.get("token"):
            self.log("  WARNING: No auth tokens detected — registration may be incomplete")

        return {
            "email": email,
            "password": password,
            "tokens": tokens,
            "status": "success" if tokens else "partial",
        }

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
        # fallback: first visible input on page
        el = page.wait_for_selector("input", timeout=10000)
        if el and el.is_visible():
            return el
        raise RuntimeError("Could not find any visible input field on the page")

    @staticmethod
    def _wait_for_otp_input(page, timeout_ms: int = 15000):
        """Wait for OTP/code input to appear. Returns element or None."""
        otp_selectors = [
            'input[placeholder*="code" i]',
            'input[placeholder*="Code" i]',
            'input[placeholder*="验证码" i]',
            'input[name="code"]',
            'input[name="otp"]',
            'input[name="verification_code"]',
            'input[type="tel"]',
            'input[data-testid*="otp" i]',
            'input[inputmode="numeric"]',
            'input[maxlength="6"]',
        ]
        for sel in otp_selectors:
            try:
                el = page.wait_for_selector(sel, timeout=timeout_ms)
                if el and el.is_visible():
                    return el
            except Exception:
                continue
        return None

    @staticmethod
    def _find_submit(page):
        """Find and return submit/continue button."""
        selectors = [
            'button[type="submit"]',
            'button:has-text("Register")',
            'button:has-text("Sign up")',
            'button:has-text("Continue")',
            'button:has-text("Next")',
            'button:has-text("注册")',
            'button:has-text("Verify")',
            'button:has-text("验证")',
        ]
        for sel in selectors:
            try:
                el = page.wait_for_selector(sel, timeout=3000)
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
