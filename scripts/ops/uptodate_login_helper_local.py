#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local helper UI for logging into a remote UpToDate Chrome CDP session.

Run this on the Windows/local machine while an SSH tunnel maps the remote
Chrome CDP port, for example:
  ssh -N -L 19222:127.0.0.1:9222 zhangyue@10.8.0.22
  python scripts/uptodate_login_helper_local.py --cdp-url http://127.0.0.1:19222

The helper keeps credentials in process memory only and forwards typed values
to the already running browser page through Playwright/CDP.
"""

from __future__ import annotations

import argparse
import base64
import html
import re
import threading
from dataclasses import dataclass
from typing import Any, Iterable

from flask import Flask, Response, redirect, render_template_string, request, url_for
from playwright.sync_api import Page, sync_playwright


DEFAULT_UPTODATE_URL = "https://www.uptodate.cn/contents/search"


@dataclass
class BrowserState:
    cdp_url: str
    uptodate_url: str
    lock: threading.Lock
    playwright: Any = None
    browser: Any = None
    page: Page | None = None
    last_message: str = ""


HTML_PAGE = r"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UpToDate 远程登录辅助</title>
  <style>
    body { font-family: system-ui, -apple-system, "Segoe UI", Arial, sans-serif; margin: 24px; color: #17202a; }
    main { max-width: 980px; margin: 0 auto; }
    .grid { display: grid; grid-template-columns: 340px 1fr; gap: 20px; align-items: start; }
    form, .panel { border: 1px solid #d8dee4; border-radius: 8px; padding: 16px; background: #fff; }
    label { display: block; font-size: 13px; margin: 12px 0 6px; color: #344054; }
    input { width: 100%; box-sizing: border-box; padding: 9px 10px; border: 1px solid #c9d1d9; border-radius: 6px; font-size: 14px; }
    button { margin-top: 12px; margin-right: 8px; padding: 8px 12px; border: 1px solid #1f6feb; border-radius: 6px; background: #1f6feb; color: #fff; cursor: pointer; }
    button.secondary { background: #fff; color: #1f2328; border-color: #c9d1d9; }
    .msg { margin: 12px 0; padding: 10px; background: #f6f8fa; border-radius: 6px; white-space: pre-wrap; }
    img { width: 100%; border: 1px solid #d8dee4; border-radius: 8px; background: #f6f8fa; }
    code { background: #f6f8fa; padding: 2px 4px; border-radius: 4px; }
  </style>
</head>
<body>
<main>
  <h2>UpToDate 远程登录辅助</h2>
  <p>连接 CDP: <code>{{ cdp_url }}</code></p>
  <p>当前页面: <code>{{ page_url }}</code></p>
  {% if message %}<div class="msg">{{ message }}</div>{% endif %}
  <div class="grid">
    <form method="post" action="/fill">
      <label>手机号 / 账号</label>
      <input name="username" autocomplete="username">
      <label>密码</label>
      <input name="password" type="password" autocomplete="current-password">
      <button type="submit">填入账号密码并点登录</button>
      <button class="secondary" name="send_code" value="1" type="submit">填入后尝试获取验证码</button>
    </form>
    <div class="panel">
      <form method="post" action="/code">
        <label>短信验证码</label>
        <input name="code" autocomplete="one-time-code">
        <button type="submit">填入验证码并提交</button>
      </form>
      <form method="post" action="/cookies">
        <button class="secondary" type="submit">接受所有 Cookie</button>
      </form>
      <form method="post" action="/nav">
        <button class="secondary" type="submit">打开 UpToDate 搜索页</button>
      </form>
      <form method="post" action="/login">
        <button class="secondary" type="submit">打开登录页</button>
      </form>
      <form method="post" action="/reset-login">
        <button class="secondary" type="submit">重新登录并发送新验证码</button>
      </form>
      <form method="post" action="/refresh">
        <button class="secondary" type="submit">刷新截图</button>
      </form>
    </div>
  </div>
  <h3>服务器浏览器截图</h3>
  {% if screenshot %}
    <img src="data:image/png;base64,{{ screenshot }}">
  {% else %}
    <p>暂无截图。</p>
  {% endif %}
</main>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cdp-url", default="http://127.0.0.1:19222")
    parser.add_argument("--uptodate-url", default=DEFAULT_UPTODATE_URL)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18088)
    return parser.parse_args()


def make_app(state: BrowserState) -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index() -> str:
        page = ensure_page(state)
        shot = screenshot_b64(page)
        return render_template_string(
            HTML_PAGE,
            cdp_url=state.cdp_url,
            page_url=page.url if page else "",
            message=state.last_message,
            screenshot=shot,
        )

    @app.post("/nav")
    def nav() -> Response:
        with state.lock:
            page = ensure_page(state)
            page.goto(state.uptodate_url, wait_until="domcontentloaded", timeout=60000)
            state.last_message = f"已打开: {page.url}"
        return redirect(url_for("index"))

    @app.post("/login")
    def login() -> Response:
        with state.lock:
            page = ensure_page(state)
            try:
                page.get_by_role("link", name=re.compile("登录|Sign in|Log in", re.I)).first.click(timeout=5000)
                page.wait_for_load_state("domcontentloaded", timeout=30000)
                state.last_message = f"已点击登录入口: {page.url}"
            except Exception:
                page.goto("https://www.uptodate.cn/login", wait_until="domcontentloaded", timeout=60000)
                state.last_message = f"已打开登录页: {page.url}"
        return redirect(url_for("index"))

    @app.post("/reset-login")
    def reset_login() -> Response:
        with state.lock:
            page = ensure_page(state)
            try:
                page.goto("https://www.uptodate.cn/logout", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1000)
            except Exception:
                pass
            page.goto("https://www.uptodate.cn/login", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1000)
            state.last_message = f"已重置到登录页: {page.url}。请重新填账号密码。"
        return redirect(url_for("index"))

    @app.post("/refresh")
    def refresh() -> Response:
        return redirect(url_for("index"))

    @app.post("/fill")
    def fill() -> Response:
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        send_code = request.form.get("send_code") == "1"
        with state.lock:
            page = ensure_page(state)
            if "/login" not in page.url:
                page.goto("https://www.uptodate.cn/login", wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(1000)
            actions: list[str] = []
            if username:
                actions.append(fill_first(page, username, username_selectors()))
            if password:
                actions.append(fill_first(page, password, password_selectors()))
            if send_code:
                actions.append(click_text(page, code_button_texts()))
            else:
                actions.append(click_text(page, login_button_texts()))
            state.last_message = "\n".join(a for a in actions if a) or "没有执行动作。"
        return redirect(url_for("index"))

    @app.post("/code")
    def code() -> Response:
        code_value = request.form.get("code", "")
        with state.lock:
            page = ensure_page(state)
            actions = []
            actions.append(accept_cookies(page))
            if code_value:
                actions.append(fill_first(page, code_value, code_selectors()))
            actions.append(submit_verification(page))
            state.last_message = "\n".join(a for a in actions if a) or "没有执行动作。"
        return redirect(url_for("index"))

    @app.post("/cookies")
    def cookies() -> Response:
        with state.lock:
            page = ensure_page(state)
            state.last_message = accept_cookies(page)
        return redirect(url_for("index"))

    return app


def ensure_page(state: BrowserState) -> Page:
    if state.playwright is None:
        state.playwright = sync_playwright().start()
    if state.browser is None:
        state.browser = state.playwright.chromium.connect_over_cdp(state.cdp_url)
    contexts = state.browser.contexts
    if not contexts:
        raise RuntimeError("CDP browser has no context.")
    pages = contexts[0].pages
    state.page = pages[0] if pages else contexts[0].new_page()
    return state.page


def screenshot_b64(page: Page | None) -> str:
    if page is None:
        return ""
    try:
        data = page.screenshot(full_page=False, timeout=10000)
        return base64.b64encode(data).decode("ascii")
    except Exception as exc:
        return ""


def fill_first(page: Page, value: str, selectors: Iterable[str]) -> str:
    errors = []
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if loc.count() > 0 and loc.is_visible(timeout=1000):
                loc.fill(value, timeout=5000)
                return f"已填写: {selector}"
        except Exception as exc:
            errors.append(f"{selector}: {exc}")
    kind = "password" if any("password" in s.lower() for s in selectors) else "text"
    fallback = page.evaluate(
        """([kind, value]) => {
          const visible = (e) => {
            const r = e.getBoundingClientRect();
            return !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length) &&
              r.width > 10 && r.height > 10 && !e.disabled && !e.readOnly;
          };
          let el = null;
          if (kind === 'password') {
            el = document.querySelector('#password') ||
              Array.from(document.querySelectorAll('input[type="password"]')).find(visible);
          } else {
            el = document.querySelector('#userName') ||
              Array.from(document.querySelectorAll('input[type="text"], input[type="tel"], input[type="email"]'))
                .find(e => visible(e) && e.id !== 'tbSearch');
          }
          if (!el) return '';
          el.focus();
          el.value = value;
          el.dispatchEvent(new Event('input', {bubbles: true}));
          el.dispatchEvent(new Event('change', {bubbles: true}));
          return el.id || el.name || el.type || el.tagName;
        }""",
        [kind, value],
    )
    if fallback:
        return f"已填写兜底输入框: {fallback}"
    return "没有找到可填写输入框。"


def click_text(page: Page, texts: Iterable[str]) -> str:
    for text in texts:
        try:
            pattern = re.compile(re.escape(text), re.I)
            loc = page.get_by_role("button", name=pattern).first
            if loc.count() > 0 and loc.is_visible(timeout=1000):
                loc.click(timeout=5000)
                page.wait_for_timeout(1000)
                return f"已点击按钮: {text}"
        except Exception:
            pass
        try:
            pattern = re.compile(re.escape(text), re.I)
            loc = page.get_by_role("link", name=pattern).first
            if loc.count() > 0 and loc.is_visible(timeout=1000):
                loc.click(timeout=5000)
                page.wait_for_timeout(1000)
                return f"已点击链接: {text}"
        except Exception:
            pass
        try:
            loc = page.locator(f"a:has-text('{text}')").first
            if loc.count() > 0 and loc.is_visible(timeout=1000):
                loc.click(timeout=5000)
                page.wait_for_timeout(1000)
                return f"已点击链接: {text}"
        except Exception:
            pass
        try:
            loc = page.get_by_text(text, exact=False).first
            if loc.count() > 0 and loc.is_visible(timeout=1000):
                loc.click(timeout=5000)
                page.wait_for_timeout(1000)
                return f"已点击文本: {text}"
        except Exception:
            pass
    return "没有找到可点击按钮。"


def accept_cookies(page: Page) -> str:
    selectors = [
        "#onetrust-accept-btn-handler",
        "button:has-text('接受所有 Cookie')",
        "button:has-text('Accept All Cookies')",
        "button:has-text('Accept all cookies')",
        "button:has-text('Accept All')",
    ]
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if loc.count() > 0 and loc.is_visible(timeout=1000):
                loc.click(timeout=5000)
                page.wait_for_timeout(500)
                return f"已接受 Cookie: {selector}"
        except Exception:
            pass
    return "没有看到需要接受的 Cookie 弹窗。"


def submit_verification(page: Page) -> str:
    before = page.url
    selectors = [
        ".otc-submit-button",
        "button[type='submit']:has-text('提交')",
        "input[type='submit']",
        "button[type='submit']",
    ]
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if loc.count() > 0 and loc.is_visible(timeout=1000):
                loc.click(timeout=5000)
                try:
                    page.wait_for_url(lambda url: url != before, timeout=15000)
                except Exception:
                    page.wait_for_timeout(1500)
                return f"已点击验证码提交按钮: {selector}; 当前页面: {page.url}"
        except Exception:
            pass
    return click_text(page, verify_button_texts())


def username_selectors() -> list[str]:
    return [
        "#userName",
        "input[type='tel']",
        "input[type='email']",
        "input[type='text'][autocomplete='username']",
        "input[name*='phone' i]",
        "input[name*='mobile' i]",
        "input[name*='user' i]",
        "input[id*='phone' i]",
        "input[id*='mobile' i]",
        "input[id*='user' i]",
        "input[placeholder*='手机']",
        "input[placeholder*='手机号']",
        "input[placeholder*='账号']",
        "input[placeholder*='用户名']",
        "input[placeholder*='邮箱']",
    ]


def password_selectors() -> list[str]:
    return [
        "#password",
        "input[type='password']",
        "input[name*='password' i]",
        "input[id*='password' i]",
        "input[placeholder*='密码']",
    ]


def code_selectors() -> list[str]:
    return [
        "#code",
        "input[autocomplete='one-time-code']",
        "input[name*='code' i]",
        "input[id*='code' i]",
        "input[placeholder*='验证码']",
        "input[placeholder*='短信']",
        "input[type='text']",
    ]


def login_button_texts() -> list[str]:
    return ["登录", "登入", "Sign in", "Log in", "继续", "下一步"]


def code_button_texts() -> list[str]:
    return ["获取验证码", "发送验证码", "发送短信", "获取短信", "Send code", "继续", "下一步"]


def verify_button_texts() -> list[str]:
    return ["登录", "验证", "提交", "确定", "继续", "下一步", "Verify", "Submit"]


def main() -> None:
    args = parse_args()
    state = BrowserState(
        cdp_url=args.cdp_url,
        uptodate_url=args.uptodate_url,
        lock=threading.Lock(),
    )
    app = make_app(state)
    print(f"Open http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False, threaded=False)


if __name__ == "__main__":
    main()
