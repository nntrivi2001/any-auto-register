"""Qwen (chat.qwen.ai) platform plugin for Any Auto Register."""

from core.base_platform import BasePlatform, Account, AccountStatus, RegisterConfig
from core.base_mailbox import BaseMailbox
from core.registry import register


@register
class QwenPlatform(BasePlatform):
    name = "qwen"
    display_name = "Qwen"
    version = "1.0.0"
    # Qwen registration requires browser automation (OTP + Turnstile)
    supported_executors = ["headless", "headed"]

    def __init__(self, config: RegisterConfig = None, mailbox: BaseMailbox = None):
        super().__init__(config)
        self.mailbox = mailbox

    def register(self, email: str, password: str = None) -> Account:
        from platforms.qwen.core import QwenRegister

        log = getattr(self, "_log_fn", print)

        mail_acct = self.mailbox.get_email() if self.mailbox else None
        email = email or (mail_acct.email if mail_acct else None)
        if not email:
            raise RuntimeError("Qwen registration requires an email address")

        log(f"邮箱: {email}")
        before_ids = self.mailbox.get_current_ids(mail_acct) if mail_acct else set()
        otp_timeout = self.get_mailbox_otp_timeout()

        def otp_cb():
            log("等待 OTP 验证码...")
            code = self.mailbox.wait_for_code(
                mail_acct,
                keyword="",
                timeout=otp_timeout,
                before_ids=before_ids,
            )
            if code:
                log(f"验证码: {code}")
            return code

        with self._make_executor() as ex:
            reg = QwenRegister(executor=ex, log_fn=log)
            result = reg.register(
                email=email,
                password=password,
                otp_callback=otp_cb if self.mailbox else None,
            )

        tokens = result.get("tokens", {})
        access_token = tokens.get("access_token", tokens.get("token", ""))
        refresh_token = tokens.get("refresh_token", tokens.get("refreshToken", ""))

        return Account(
            platform="qwen",
            email=result["email"],
            password=result["password"],
            token=access_token,
            status=AccountStatus.REGISTERED,
            extra={
                "refresh_token": refresh_token,
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
            # Try to access user profile endpoint
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
            {"id": "get_user_info", "label": "获取用户信息", "params": []},
        ]

    def execute_action(self, action_id: str, account: Account, params: dict) -> dict:
        """Execute platform-specific actions."""
        if action_id == "get_user_info":
            from curl_cffi import requests as curl_req

            token = account.token
            if not token:
                return {"ok": False, "error": "账号缺少 token"}

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
                return {"ok": False, "error": f"获取失败: HTTP {r.status_code}"}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        raise NotImplementedError(f"未知操作: {action_id}")
