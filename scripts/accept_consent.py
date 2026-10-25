"""Accept a Foundry tool connection's per-user OAuth consent link in a signed-in browser (Playwright over CDP).

Usage: python scripts/accept_consent.py <consent-url> [--account <upn>] [--cdp http://127.0.0.1:9334]
           [--wait-for-person <seconds>]

The browser must already be signed in as the user who should consent (the account picker is answered with
--account). The script never types a password: if a password or MFA prompt appears, it stops and says so, or with
--wait-for-person waits that long for the person to finish it (approve the sign-in on their phone, using the number
it prints, or type the password in the browser window). Screenshots of every step go to <work>/consent/.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

DONE = re.compile(r"(you can close|close this window|consent (was )?granted|authorization complete|you('| a)re all set|"
                  r"successfully (connected|authorized|signed in))", re.I)
# An error page also says "close this window": check for failure first.
FAILED = re.compile(r"(authentication failed|something went wrong|access denied|consent (was )?denied|"
                    r"error: code [0-9a-f]+ not found)", re.I)
STOP = re.compile(r"(enter password|verify your identity|approve sign in request|more information required|"
                  r"use your password|stay signed in\?)", re.I)
# The connector consent page's anti-phishing confirmation. It is ticked only with --trust-connection, and only when the
# page names a connection containing that text (this deployment's own connections).
CONFIRMATION = re.compile(r"confirmation required", re.I)
TRUST_CHECKBOX = re.compile(r"I have verified this request and trust the source", re.I)
BUTTONS = ["Accept", "Allow", "Yes", "Continue", "Sign in", "Consent", "Next", "Confirm", "Grant", "Authorize", "Approve"]


async def sign_in_number(page) -> str:
    """The number Microsoft Authenticator asks for (number matching), if the sign-in page shows one."""
    for selector in ("#idRichContext_DisplaySign", ".displaySign", "[data-testid='displaySign']"):
        element = page.locator(selector)
        try:
            if await element.count():
                value = (await element.first.inner_text(timeout=2_000)).strip()
                if value.isdigit():
                    return value
        except Exception:
            continue
    return ""


def person_prompt(text: str) -> str:
    if re.search(r"enter password|use your password", text, re.I):
        return "type the password in the browser window."
    return "approve the sign-in request in Microsoft Authenticator."


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--account", default=os.environ.get("DEMO_ADVISOR_UPN", ""))
    parser.add_argument("--cdp", default=os.environ.get("TEAM_CDP", "http://127.0.0.1:9334"))
    parser.add_argument("--timeout", type=int, default=150)
    parser.add_argument("--wait-for-person", type=int, default=0,
                        help="seconds to wait for a person to finish a password or MFA prompt instead of stopping")
    parser.add_argument("--trust-connection", default="",
                        help="confirm the consent page's 'I have verified this request' only for a connection whose "
                             "name contains this text (for example this deployment's suffix); otherwise stop there")
    args = parser.parse_args()
    shots = Path(os.environ.get("TEAM_WORK_DIR", ".")) / "consent"
    shots.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%H%M%S")

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(args.cdp)
        context = browser.contexts[0]
        page = await context.new_page()
        pages_seen = [page]
        context.on("page", lambda new: pages_seen.append(new))
        await page.goto(args.url, wait_until="domcontentloaded")
        deadline = time.monotonic() + args.timeout
        step = 0
        last_text = ""
        waiting_since = 0.0
        while time.monotonic() < deadline:
            await asyncio.sleep(2.5)
            for current in list(pages_seen):
                if current.is_closed():
                    continue
                try:
                    text = (await current.inner_text("body", timeout=5_000))[:4_000]
                except Exception:
                    continue
                step += 1
                await current.screenshot(path=str(shots / f"{stamp}-{step:02d}.png"))
                if STOP.search(text):
                    # The browser's own saved credential may have filled the password (never typed here): sign in.
                    password = current.locator("input[type=password]")
                    if await password.count() and len(await password.first.input_value()) > 0:
                        submit = current.locator("input[type=submit], button[type=submit]")
                        if await submit.count():
                            await submit.first.click()
                            print("signed in with the browser's saved credential")
                            continue
                    if re.search(r"stay signed in\?", text, re.I):
                        yes = current.locator("input[type=submit][value='Yes'], button:has-text('Yes')")
                        if await yes.count():
                            await yes.first.click()
                            print("answered Stay signed in: Yes")
                            continue
                    if args.wait_for_person:
                        if not waiting_since:
                            waiting_since = time.monotonic()
                            deadline = max(deadline, waiting_since + args.wait_for_person)
                            number = await sign_in_number(current)
                            print(f"PERSON NEEDED: {person_prompt(text)}"
                                  + (f" Authenticator number: {number}" if number else ""), flush=True)
                        elif time.monotonic() - waiting_since > args.wait_for_person:
                            print(f"STOPPED: nobody finished the prompt within {args.wait_for_person}s: {current.url[:120]}")
                            return 2
                        continue
                    print(f"STOPPED: the browser needs a person (password, MFA or a prompt): {current.url[:120]}")
                    return 2
                if waiting_since:
                    print("the person finished the prompt; continuing", flush=True)
                    waiting_since = 0.0
                # Account picker first: choose the signed-in account.
                if args.account and re.search(r"pick an account", text, re.I):
                    tile = current.get_by_text(re.compile(re.escape(args.account), re.I))
                    if await tile.count():
                        await tile.first.click()
                        print(f"picked account {args.account}")
                        continue
                if FAILED.search(text):
                    print(f"CONSENT FAILED: {text.strip()[:200]!r}")
                    return 3
                if CONFIRMATION.search(text) and TRUST_CHECKBOX.search(text):
                    trusted = bool(args.trust_connection) and args.trust_connection.lower() in text.lower()
                    if not trusted:
                        if args.wait_for_person:
                            if not waiting_since:
                                waiting_since = time.monotonic()
                                deadline = max(deadline, waiting_since + args.wait_for_person)
                                print("PERSON NEEDED: tick 'I have verified this request and trust the source' and "
                                      "click Allow access in the browser window.", flush=True)
                            elif time.monotonic() - waiting_since > args.wait_for_person:
                                print(f"STOPPED: nobody confirmed the request within {args.wait_for_person}s")
                                return 2
                            continue
                        print("STOPPED: the consent page asks a person to confirm they trust this connection "
                              f"(--trust-connection not given or not matched): {current.url[:120]}")
                        return 2
                    checkbox = current.locator("input[type=checkbox]")
                    if await checkbox.count() and not await checkbox.first.is_checked():
                        await checkbox.first.check()
                        print(f"confirmed the request for this deployment's connection (*{args.trust_connection}*)")
                        continue
                    allow = current.get_by_role("button", name=re.compile(r"^\s*Allow access", re.I))
                    if not await allow.count():
                        allow = current.get_by_role("link", name=re.compile(r"^\s*Allow access", re.I))
                    if await allow.count() and await allow.first.is_enabled() and \
                            await allow.first.get_attribute("aria-disabled") != "true":
                        await allow.first.click()
                        print("clicked Allow access")
                    continue
                if DONE.search(text):
                    print(f"CONSENT DONE: {text.strip()[:200]!r}")
                    return 0
                clicked = False
                for label in BUTTONS:
                    button = current.get_by_role("button", name=re.compile(rf"^\s*{label}\b", re.I))
                    if await button.count() and await button.first.is_enabled():
                        await button.first.click()
                        print(f"clicked {label} on {current.url[:80]}")
                        clicked = True
                        break
                    link = current.get_by_role("link", name=re.compile(rf"^\s*{label}\b", re.I))
                    if await link.count() and await link.first.get_attribute("aria-disabled") != "true":
                        await link.first.click()
                        print(f"clicked link {label} on {current.url[:80]}")
                        clicked = True
                        break
                if not clicked and text != last_text:
                    print(f"waiting: {current.url[:100]} | {text.strip()[:160]!r}")
                    last_text = text
        print(f"TIMEOUT after {args.timeout}s; screenshots in {shots}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
