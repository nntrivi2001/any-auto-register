"""Qwen (chat.qwen.ai) platform plugin for Any Auto Register.

Registration flow (verified):
    - Fill form (Name + Email + Password + Confirm + Terms) → Submit
    - JWT token issued immediately in "token" cookie
    - Activation email sent — account needs activation link visit
    - Activation URL is called via API to complete registration
"""

from core.base_platform import BasePlatform, Account, AccountStatus, RegisterConfig
from core.base_mailbox import BaseMailbox
from core.registry import register


@register
class QwenPlatform(BasePlatform):
    name = "qwen"
    display_name = "Qwen"
    version = "1.0.0"
    # Qwen requires browser automation (form fill + submit)
    supported_executors = ["headless", "headed"]

    def __init__(self, config: RegisterConfig = None, mailbox: BaseMailbox = None):
        super().__init__(config)
        self.mailbox = mailbox

    def register(self, email: str, password: str = None) -> Account:
        from platforms.qwen.core import (
            QwenRegister,
            call_activation_api,
            extract_activation_link,
        )

        log = getattr(self, "_log_fn", print)

        mail_acct = self.mailbox.get_email() if self.mailbox else None
        email = email or (mail_acct.email if mail_acct else None)
        if not email:
            raise RuntimeError("Qwen registration requires an email address")

        log(f"Email: {email}")
        before_ids = self.mailbox.get_current_ids(mail_acct) if mail_acct else set()
        otp_timeout = self.get_mailbox_otp_timeout()

        with self._make_executor() as ex:
            reg = QwenRegister(executor=ex, log_fn=log)
            result = reg.register(email=email, password=password)

        if result.get("status") != "success":
            return Account(
                platform="qwen",
                email=email,
                password=password or "",
                token="",
                status=AccountStatus.REGISTERED,
                extra={"error": "Registration failed — no token cookie"},
            )

        tokens = result.get("tokens", {})
        # Token is in cookie:token from Qwen
        access_token = (
            tokens.get("token")
            or tokens.get("cookie:token")
            or tokens.get("access_token", "")
        )

        # Try activation if mailbox available
        activated = False
        if self.mailbox and mail_acct:
            log("Waiting for activation email...")
            activation_link = None
            for _ in range(min(10, otp_timeout // 5)):
                import time
                time.sleep(3)
                try:
                    messages = self.mailbox.get_messages(mail_acct, before_ids=before_ids)
                    for msg in messages:
                        body = self.mailbox.get_message_body(mail_acct, msg.get("id")) or ""
                        link = extract_activation_link(body)
                        if link:
                            activation_link = link
                            break
                    if activation_link:
                        break
                except Exception:
                    continue

            if activation_link:
                log(f"Activation link found, activating...")
                act_result = call_activation_api(activation_link)
                activated = act_result.get("ok", False)
                log(f"Activation: {'SUCCESS' if activated else 'FAILED'}")

        return Account(
            platform="qwen",
            email=result["email"],
            password=result["password"],
            token=access_token,
            status=AccountStatus.REGISTERED,
            extra={
                "activated": activated,
                "full_name": result.get("full_name", ""),
                "raw_tokens": tokens,
            },
        )

    def check_valid(self, account: Account) -> bool:
        """Check if Qwen account token is still valid."""
        from curl_cffi import requests as curl_req

        token = account.token
        if not token:
            return False

        try:
            r = curl_req.get(
                "https://chat.qwen.ai/api/v1/user/profile",
                headers={
                    "Authorization": f"Bearer {token}",
                    "user-agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                },
                impersonate="chrome124",
                timeout=15,
            )
            return r.status_code == 200
        except Exception:
            return False

    def get_platform_actions(self) -> list:
        """Return platform-specific actions."""
        return [
            {"id": "activate_account", "label": "Activate Account", "params": []},
            {"id": "get_user_info", "label": "Get User Info", "params": []},
        ]

    def execute_action(self, action_id: str, account: Account, params: dict) -> dict:
        """Execute platform-specific actions."""
        from curl_cffi import requests as curl_req
        from platforms.qwen.core import call_activation_api

        if action_id == "activate_account":
            # Try to find activation link from email and activate
            if not self.mailbox:
                return {"ok": False, "error": "No mailbox configured for activation"}

            email = account.email
            password = account.password or ""
            mail_acct = self.mailbox.get_email()
            if not mail_acct or mail_acct.email != email:
                return {"ok": False, "error": f"No mailbox for {email}"}

            try:
                from platforms.qwen.core import extract_activation_link
                import time

                before_ids = self.mailbox.get_current_ids(mail_acct)
                activation_link = None
                for _ in range(24):  # 120s
                    time.sleep(5)
                    messages = self.mailbox.get_messages(mail_acct, before_ids=before_ids)
                    for msg in messages:
                        body = self.mailbox.get_message_body(mail_acct, msg.get("id")) or ""
                        link = extract_activation_link(body)
                        if link:
                            activation_link = link
                            break
                    if activation_link:
                        break

                if activation_link:
                    act_result = call_activation_api(activation_link)
                    if act_result.get("ok"):
                        return {"ok": True, "message": "Account activated"}
                    return {"ok": False, "error": f"Activation failed: {act_result}"}
                return {"ok": False, "error": "No activation email found"}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        if action_id == "get_user_info":
            token = account.token
            if not token:
                return {"ok": False, "error": "Account missing token"}

            try:
                r = curl_req.get(
                    "https://chat.qwen.ai/api/v1/user/profile",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "user-agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/131.0.0.0 Safari/537.36"
                        ),
                    },
                    impersonate="chrome124",
                    timeout=15,
                )
                if r.status_code == 200:
                    return {"ok": True, "data": r.json()}
                return {"ok": False, "error": f"Failed: HTTP {r.status_code}"}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        raise NotImplementedError(f"Unknown action: {action_id}")
