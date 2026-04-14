#!/usr/bin/env python3
"""Register 30 Qwen accounts + OAuth via 9router.

Proven flow (tested and working):
  1. Get device code from 9router FIRST (includes client=qwen-code param)
  2. Create temp email via mail.tm
  3. Register Qwen account → auto logged in
  4. Open activation link in BROWSER (not API) → confirms activation
  5. Open authorize page with FULL verification_uri_complete
  6. Click "Confirm" button
  7. Poll for token via 9router API
"""

import json
import random
import re
import string
import sys
import time
import requests as req
from urllib.parse import urlparse, parse_qs
from uuid import uuid4

from playwright.sync_api import sync_playwright

# --- Config ---
QWEN_REGISTER = "https://chat.qwen.ai/auth?mode=register"
PASSWORD = "*dbs3211"
TARGET = 30  # Target number of Qwen accounts in 9router. Adjust as needed.

MAILTM_API = "https://api.mail.tm"
NINEROUTER = "http://localhost:20128"
QWEN_DEVICE_CODE = f"{NINEROUTER}/api/oauth/qwen/device-code"
QWEN_POLL = f"{NINEROUTER}/api/oauth/qwen/poll"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class MailTM:
    def __init__(self):
        self.address = None
        self.password = None
        self.token = None

    def create_account(self, retries=3):
        r = req.get(f"{MAILTM_API}/domains", timeout=15)
        domains = [d["domain"] for d in r.json().get("hydra:member", [])]
        if not domains:
            raise RuntimeError("No mail.tm domains")
        domain = random.choice(domains)
        username = f"qwen{''.join(random.choices(string.ascii_lowercase + string.digits, k=10))}"
        self.address = f"{username}@{domain}"
        self.password = f"Pass{random.randint(10000, 99999)}!"

        for attempt in range(retries):
            r = req.post(f"{MAILTM_API}/accounts", json={"address": self.address, "password": self.password}, timeout=15)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1)); continue
            if r.status_code != 201:
                raise RuntimeError(f"mail.tm create failed: {r.text[:100]}")
            break
        else:
            raise RuntimeError("mail.tm rate limited")

        for attempt in range(retries):
            r = req.post(f"{MAILTM_API}/token", json={"address": self.address, "password": self.password}, timeout=15)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1)); continue
            if r.status_code != 200:
                raise RuntimeError("mail.tm token failed")
            break
        else:
            raise RuntimeError("mail.tm token rate limited")

        self.token = r.json().get("token")
        return self.address

    def get_messages(self):
        r = req.get(f"{MAILTM_API}/messages", headers={"Authorization": f"Bearer {self.token}"}, timeout=15)
        return r.json().get("hydra:member", []) if r.status_code == 200 else []

    def get_message(self, msg_id):
        r = req.get(f"{MAILTM_API}/messages/{msg_id}", headers={"Authorization": f"Bearer {self.token}"}, timeout=15)
        return r.json() if r.status_code == 200 else {}

    def get_activation_link(self):
        msgs = self.get_messages()
        for msg in msgs:
            full = self.get_message(msg["id"])
            for key in ["text", "html"]:
                body = full.get(key, "")
                if isinstance(body, list):
                    body = " ".join(str(b) for b in body)
                if not body: continue
                urls = re.findall(r'https?://[^\s"\'<>\]]+', body)
                for u in urls:
                    if "activate" in u.lower() and "qwen" in u.lower():
                        return u
        return None


def count_9router_qwen():
    try:
        r = req.get(f"{NINEROUTER}/api/providers", timeout=10)
        if r.status_code == 200:
            conns = r.json().get("connections", [])
            return len([c for c in conns if c.get("provider") == "qwen"])
    except: pass
    return 0


def main():
    print(f"=== Qwen: Register {TARGET} accounts + OAuth ===")
    print(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    current = count_9router_qwen()
    print(f"Current Qwen accounts in 9router: {current}")
    need = TARGET - current
    if need <= 0:
        print(f"Already have {current} >= {TARGET}!")
        return
    print(f"Need to create: {need}\n")

    success = 0
    fail = 0
    results = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
        ])

        for i in range(need + 20):
            acc_num = i + 1
            print(f"\n{'='*40}")
            print(f"--- Account #{acc_num} (need {need - success} more) ---")
            print(f"{'='*40}")

            # 1. Create email
            mailtm = MailTM()
            try:
                email = mailtm.create_account()
            except Exception as e:
                print(f"  MailTM error: {e}")
                fail += 1
                time.sleep(5)
                continue

            print(f"  Email: {email}")

            # Create context and page
            ctx = browser.new_context(viewport={"width": 1280, "height": 800}, user_agent=UA)
            ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            page = ctx.new_page()

            try:
                # 2. Get device code FIRST (before registration takes too long)
                print(f"  Getting device code...")
                device = None
                for dc_retry in range(3):
                    try:
                        r = req.get(QWEN_DEVICE_CODE, timeout=30)
                        if r.status_code == 200:
                            device = r.json()
                            break
                    except Exception as e:
                        print(f"    Device code retry {dc_retry+1}: {e}")
                        time.sleep(3)

                if not device:
                    print(f"  FAILED: device code")
                    fail += 1
                    results.append({"email": email, "status": "failed_device_code"})
                    page.close(); ctx.close()
                    time.sleep(3)
                    continue

                user_code = device.get("userCode") or device.get("user_code")
                device_code = device.get("deviceCode") or device.get("device_code")
                code_verifier = device.get("codeVerifier") or device.get("code_verifier")
                verify_uri = device.get("verification_uri_complete") or device.get("verificationUriComplete")
                print(f"  User code: {user_code}")

                # 3. Register Qwen
                print(f"  Registering Qwen...")
                page.goto(QWEN_REGISTER, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3000)

                try:
                    page.locator('input[name="username"]').wait_for(state="visible", timeout=15000)
                    page.locator('input[name="username"]').fill(email.split("@")[0].replace("_", " ").title())
                    page.wait_for_timeout(100)
                    page.locator('input[name="email"]').fill(email)
                    page.wait_for_timeout(100)
                    page.locator('input[name="password"]').fill(PASSWORD)
                    page.wait_for_timeout(100)
                    page.locator('input[name="checkPassword"]').fill(PASSWORD)
                    page.wait_for_timeout(500)
                except Exception as e:
                    print(f"    Form fill failed: {e}")
                    fail += 1
                    results.append({"email": email, "status": "failed_form_fill"})
                    page.close(); ctx.close()
                    time.sleep(3)
                    continue

                # Tick checkbox
                for cb in page.query_selector_all('input[type="checkbox"]'):
                    try:
                        if cb.is_visible() and not cb.evaluate("(el) => el.checked"):
                            cb.click()
                    except: pass
                page.wait_for_timeout(500)

                # Submit
                submit = page.query_selector('button[type="submit"]')
                if submit and not submit.evaluate("(el) => el.disabled"):
                    submit.click()
                    page.wait_for_timeout(5000)
                    print(f"    Form submitted")
                else:
                    print(f"    Submit button disabled")
                    fail += 1
                    results.append({"email": email, "status": "failed_submit"})
                    page.close(); ctx.close()
                    time.sleep(3)
                    continue

                # Check token
                has_token = any(c["name"] == "token" and len(c["value"]) > 10 for c in ctx.cookies())
                if not has_token:
                    print(f"    FAILED: no token cookie after registration")
                    fail += 1
                    results.append({"email": email, "status": "failed_registration"})
                    page.close(); ctx.close()
                    time.sleep(3)
                    continue
                print(f"    Registered (token cookie OK)")

                # 4. Wait for activation email
                print(f"  Waiting for activation email...")
                link = None
                for attempt in range(12):
                    time.sleep(5)
                    link = mailtm.get_activation_link()
                    if link:
                        print(f"    Activation link found")
                        break

                if not link:
                    print(f"  FAILED: no activation email")
                    fail += 1
                    results.append({"email": email, "status": "failed_activation_email"})
                    page.close(); ctx.close()
                    time.sleep(3)
                    continue

                # 5. Open activation link IN BROWSER (not API!)
                print(f"  Opening activation link in browser...")
                page.goto(link, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
                print(f"    After activation URL: {page.url}")
                time.sleep(3)

                # 6. Open authorize page with FULL URI (includes client=qwen-code)
                print(f"  Opening authorize page...")
                page.goto(verify_uri, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(5000)
                print(f"    Authorize URL: {page.url}")

                # 7. Click "Confirm" button
                clicked = False
                try:
                    page.locator('button:has-text("Confirm")').click()
                    clicked = True
                    print(f"    Clicked Confirm")
                except:
                    for btn in page.query_selector_all('button'):
                        try:
                            t = btn.inner_text().strip().lower()
                            if t == "confirm":
                                btn.click()
                                clicked = True
                                print(f"    Clicked Confirm (fallback)")
                                break
                        except: pass

                if not clicked:
                    # Debug
                    buttons = page.query_selector_all('button')
                    btn_texts = [btn.inner_text().strip() for btn in buttons]
                    print(f"    No Confirm button. Buttons: {btn_texts}")
                    page.screenshot(path=f"/tmp/oauth_debug_{acc_num}.png")

                page.wait_for_timeout(5000)

                # 8. Poll for token
                print(f"  Polling for OAuth token...")
                oauth_ok = False
                for attempt in range(25):
                    time.sleep(3)
                    try:
                        r = req.post(QWEN_POLL, json={
                            "deviceCode": device_code,
                            "codeVerifier": code_verifier,
                        }, timeout=30)

                        if r.status_code != 200:
                            print(f"    Poll HTTP error: {r.status_code}")
                            break

                        result = r.json()
                        if result.get("success"):
                            conn = result.get("connection", {})
                            print(f"    OAuth SUCCESS! Connection: {conn.get('id', '')[:30]}...")
                            oauth_ok = True
                            break

                        error = result.get("error", "")
                        if error in ("expired_token", "access_denied"):
                            print(f"    OAuth error: {error}")
                            break
                        if attempt % 5 == 0:
                            print(f"    Still pending... ({attempt}/25)")
                    except Exception as e:
                        print(f"    Poll exception: {e}")
                        time.sleep(3)

                if oauth_ok:
                    success += 1
                    results.append({"email": email, "status": "success"})
                else:
                    fail += 1
                    results.append({"email": email, "status": "failed_oauth"})

            except Exception as e:
                print(f"  Exception: {e}")
                fail += 1
                results.append({"email": email, "status": "exception"})

            finally:
                try: page.close()
                except: pass
                try: ctx.close()
                except: pass

            total = count_9router_qwen()
            print(f"  9router total: {total}")

            if total >= TARGET:
                print(f"\n  *** Target {TARGET} reached! ***")
                break

            time.sleep(random.uniform(3, 6))

        browser.close()

    print(f"\n{'='*50}")
    print(f"  Results Summary")
    print(f"{'='*50}")
    print(f"  Success: {success}")
    print(f"  Failed: {fail}")
    print(f"  Total in 9router: {count_9router_qwen()}")
    print(f"{'='*50}")
    for r in results:
        print(f"  [{r['status']:30s}] {r['email']}")


if __name__ == "__main__":
    main()
