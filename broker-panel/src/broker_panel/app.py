"""Broker control panel web app."""

from __future__ import annotations

import argparse
import html
import sys
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qs

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from broker_panel.client import BrokerAPIError, BrokerClient
from broker_panel.config import Config, load_config
from broker_panel.security import hash_password, sign_session, verify_password, verify_session

SESSION_COOKIE = "broker_panel_session"


def create_app(config: Config | None = None, broker: Any | None = None) -> FastAPI:
    cfg = config or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.config = cfg
        app.state.broker = broker or BrokerClient(cfg)
        yield
        if broker is None:
            await app.state.broker.aclose()

    app = FastAPI(title="Toolstack Broker Panel", version="0.1.0", lifespan=lifespan)

    @app.get("/login")
    async def login_page(request: Request):
        if _current_user(request):
            return RedirectResponse("/", status_code=303)
        return _login_response()

    @app.post("/login")
    async def login(request: Request):
        form = await _form(request)
        username = form.get("username", "")
        password = form.get("password", "")
        cfg = request.app.state.config
        if username != cfg.username or not verify_password(password, cfg.password_hash()):
            return _login_response(error="Invalid username or password", status_code=401)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            sign_session(username, cfg.session_secret(), cfg.session_ttl_seconds),
            httponly=True,
            samesite="lax",
            secure=False,
        )
        return response

    @app.post("/logout")
    async def logout():
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE)
        return response

    @app.get("/")
    async def dashboard(request: Request):
        user = _current_user(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        return await _dashboard_response(request, user=user)

    @app.post("/tools/reload")
    async def reload_tools(request: Request):
        user = _current_user(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        try:
            result = await request.app.state.broker.reload_tools()
            count = result.get("tool_count", 0)
            banner = (
                f"Reloaded tool registry: {_esc(count)} tool(s). "
                "Restart changed containers with Toolyard when needed."
            )
            return await _dashboard_response(request, user=user, banner=banner)
        except BrokerAPIError as exc:
            return await _dashboard_response(request, user=user, error=exc.detail)

    @app.post("/callers")
    async def create_caller(request: Request):
        user = _current_user(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        form = await _form(request)
        try:
            result = await request.app.state.broker.create_caller(
                form.get("name", "").strip(),
            )
            token = result["token"]
            caller = result["caller"]
            banner = (
                f"Created caller {_esc(caller['name'])}. Save this token now: "
                f"<code>{_esc(token)}</code>"
            )
            return await _dashboard_response(request, user=user, banner=banner)
        except BrokerAPIError as exc:
            return await _dashboard_response(request, user=user, error=exc.detail)

    @app.post("/callers/refresh-token")
    async def refresh_caller_token(request: Request):
        user = _current_user(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        form = await _form(request)
        try:
            result = await request.app.state.broker.refresh_caller_token(form.get("name", ""))
            token = result["token"]
            caller = result["caller"]
            banner = (
                f"Refreshed token for {_esc(caller['name'])}. Save this token now: "
                f"<code>{_esc(token)}</code>"
            )
            return await _dashboard_response(request, user=user, banner=banner)
        except BrokerAPIError as exc:
            return await _dashboard_response(request, user=user, error=exc.detail)

    @app.post("/callers/revoke")
    async def revoke_caller(request: Request):
        user = _current_user(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        form = await _form(request)
        try:
            await request.app.state.broker.revoke_caller(form.get("name", ""))
        except BrokerAPIError as exc:
            return await _dashboard_response(request, user=user, error=exc.detail)
        return RedirectResponse("/", status_code=303)

    @app.post("/tokens/revoke")
    async def revoke_token(request: Request):
        user = _current_user(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        form = await _form(request)
        try:
            await request.app.state.broker.revoke_token(form.get("hash_prefix", ""))
        except BrokerAPIError as exc:
            return await _dashboard_response(request, user=user, error=exc.detail)
        return RedirectResponse("/", status_code=303)

    @app.get("/callers/{caller}/policy")
    async def edit_caller_policy(request: Request, caller: str):
        user = _current_user(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        try:
            tools = await request.app.state.broker.get_tools()
            policy_data = await request.app.state.broker.get_caller_policy(caller)
        except BrokerAPIError as exc:
            return _page("Caller Policy", f"<p class='error'>{_esc(exc.detail)}</p>", user=user)
        return _caller_policy_response(caller, tools, policy_data, user=user)

    @app.post("/callers/{caller}/policy")
    async def save_caller_policy(request: Request, caller: str):
        user = _current_user(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        form = await _form(request)
        tools: dict[str, dict[str, dict[str, str]]] = {}
        for key, value in form.items():
            if not key.startswith("op__"):
                continue
            _, tool_id, op = key.split("__", 2)
            tools.setdefault(tool_id, {"operations": {}})["operations"][op] = value
        ttl_raw = form.get("auto_grant_ttl_seconds", "").strip()
        ttl = int(ttl_raw) if ttl_raw else None
        broker_ops = [
            value.strip()
            for value in form.get("broker_ops", "").splitlines()
            if value.strip()
        ]
        try:
            await request.app.state.broker.put_caller_policy(
                caller,
                {
                    "tools": tools,
                    "broker_ops": broker_ops,
                    "auto_grant_ttl_seconds": ttl,
                },
            )
        except BrokerAPIError as exc:
            tools_payload = await request.app.state.broker.get_tools()
            policy_data = await request.app.state.broker.get_caller_policy(caller)
            return _caller_policy_response(
                caller,
                tools_payload,
                policy_data,
                user=user,
                error=exc.detail,
            )
        return RedirectResponse(f"/callers/{caller}/policy", status_code=303)

    return app


async def _dashboard_response(
    request: Request,
    *,
    user: str,
    banner: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    broker = request.app.state.broker
    try:
        tools = await broker.get_tools()
        callers = await broker.get_callers()
        tokens = await broker.get_tokens()
        requests = await broker.get_requests()
        audit_events = await broker.get_audit()
    except BrokerAPIError as exc:
        return _page("Dashboard", f"<p class='error'>{_esc(exc.detail)}</p>", user=user)

    body = []
    if banner:
        body.append(f"<div class='banner'>{banner}</div>")
    if error:
        body.append(f"<div class='error'>{_esc(error)}</div>")
    body.append(
        """
<section class="toolbar">
  <form method="post" action="/tools/reload" class="inline-form">
    <button type="submit">Reload Tool Registry</button>
  </form>
  <form method="post" action="/callers" class="inline-form">
    <input name="name" placeholder="caller name" required>
    <button type="submit">Create Caller</button>
  </form>
</section>
"""
    )
    body.append(_cards_section(tools, callers, tokens))
    body.append(_requests_section(requests))
    body.append(_audit_section(audit_events))
    return _page("Dashboard", "".join(body), user=user)


def _cards_section(
    tools: dict[str, Any],
    callers: list[dict[str, Any]],
    tokens: list[dict[str, Any]],
) -> str:
    caller_items = "".join(
        "<tr>"
        f"<td>{_esc(c['name'])}</td>"
        f"<td>{'revoked' if c.get('revoked_at') else 'active'}</td>"
        "<td class='actions'>"
        f"<a class='button' href='/callers/{_esc(c['name'])}/policy'>Policy</a>"
        "<form method='post' action='/callers/refresh-token'>"
        f"<input type='hidden' name='name' value='{_esc(c['name'])}'>"
        "<button type='submit'>Refresh Token</button></form>"
        "<form method='post' action='/callers/revoke'>"
        f"<input type='hidden' name='name' value='{_esc(c['name'])}'>"
        "<button type='submit'>Revoke</button></form></td>"
        "</tr>"
        for c in callers
    )
    token_items = "".join(
        "<tr>"
        f"<td><code>{_esc(t['hash_prefix'])}</code></td><td>{_esc(t['caller_name'])}</td>"
        f"<td>{'revoked' if t.get('revoked_at') else 'active'}</td>"
        "<td><form method='post' action='/tokens/revoke'>"
        f"<input type='hidden' name='hash_prefix' value='{_esc(t['hash_prefix'])}'>"
        "<button type='submit'>Revoke</button></form></td>"
        "</tr>"
        for t in tokens
    )
    tool_items = "".join(
        f"<li><strong>{_esc(tid)}</strong><span>{len(tool.get('operations', []))} ops</span></li>"
        for tid, tool in tools.items()
    )
    return f"""
<section class="grid">
  <article><h2>Tools</h2><ul class="list">{tool_items}</ul></article>
  <article class="wide"><h2>Callers</h2><table><tbody>{caller_items}</tbody></table></article>
  <article class="wide"><h2>Tokens</h2><table><tbody>{token_items}</tbody></table></article>
</section>
"""


def _requests_section(requests: list[dict[str, Any]]) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{req['id']}</td><td>{_esc(req['caller'])}</td>"
        f"<td>{_esc(req['tool'])}.{_esc(req['op'])}</td><td>{_esc(req['status'])}</td>"
        "</tr>"
        for req in requests
    )
    return f"<section><h2>Recent Requests</h2><table>{rows}</table></section>"


def _audit_section(events: list[dict[str, Any]]) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{event['id']}</td><td>{_esc(event['kind'])}</td>"
        f"<td>{_esc(event.get('tool') or '')}.{_esc(event.get('op') or '')}</td>"
        "</tr>"
        for event in events
    )
    return f"<section><h2>Broker Audit</h2><table>{rows}</table></section>"


def _caller_policy_response(
    caller: str,
    tools: dict[str, Any],
    policy_data: dict[str, Any],
    *,
    user: str,
    error: str | None = None,
) -> HTMLResponse:
    tools_state = policy_data.get("tools", {})
    broker_ops = "\n".join(policy_data.get("broker_ops") or [])
    ttl = policy_data.get("auto_grant_ttl_seconds") or ""
    sections = []
    if error:
        sections.append(f"<div class='error'>{_esc(error)}</div>")
    sections.append(
        f"""
<form method="post" action="/callers/{_esc(caller)}/policy">
  <div class="policy-head">
    <a href="/">Back</a>
    <label>Grant TTL <input name="auto_grant_ttl_seconds" value="{_esc(str(ttl))}" inputmode="numeric"></label>
    <button type="button" onclick="setAll('allow')">Allow all</button>
    <button type="button" onclick="setAll('review')">Review all</button>
    <button type="button" onclick="setAll('deny')">Disable all</button>
    <button type="button" onclick="setRecommended()">Recommended</button>
    <button type="submit">Save Policy</button>
  </div>
  <section>
    <h2>Broker Ops</h2>
    <textarea name="broker_ops" rows="4" spellcheck="false">{_esc(broker_ops)}</textarea>
  </section>
"""
    )
    for tool_id, tool in tools.items():
        op_rows = []
        current_ops = tools_state.get(tool_id, {}).get("operations", {})
        tool_description = tool.get("description") or ""
        for operation in tool.get("operations", []):
            op = operation["op"]
            risk = operation["risk"]
            description = operation.get("description") or ""
            current = current_ops.get(op, "deny")
            select_name = f"op__{tool_id}__{op}"
            op_rows.append(
                "<tr>"
                f"<td>{_esc(op)}</td><td><span class='risk {_esc(risk)}'>{_esc(risk)}</span></td>"
                f"<td>{_esc(description)}</td><td>{_effect_select(select_name, current, risk)}</td>"
                "</tr>"
            )
        sections.append(
            f"""
  <section class="tool-policy">
    <div class="tool-head">
      <div><h2>{_esc(tool_id)}</h2><p>{_esc(tool_description)}</p></div>
      <div>
        <button type="button" onclick="setTool('{_esc(tool_id)}','allow')">Allow all</button>
        <button type="button" onclick="setTool('{_esc(tool_id)}','review')">Review all</button>
        <button type="button" onclick="setTool('{_esc(tool_id)}','deny')">Deny all</button>
        <button type="button" onclick="setToolRisk('{_esc(tool_id)}','read','allow')">Allow reads</button>
        <button type="button" onclick="setToolRecommended('{_esc(tool_id)}')">Recommended</button>
      </div>
    </div>
    <table><tbody>{''.join(op_rows)}</tbody></table>
  </section>
"""
    )
    sections.append("</form>")
    return _page(f"Policy {caller}", "".join(sections), user=user)


def _effect_select(name: str, current: str, risk: str) -> str:
    options = []
    for value in ("allow", "review", "deny"):
        selected = " selected" if current == value else ""
        options.append(f"<option value='{value}'{selected}>{value.title()}</option>")
    return f"<select name='{_esc(name)}' data-risk='{_esc(risk)}'>{''.join(options)}</select>"


def _login_response(error: str | None = None, status_code: int = 200) -> HTMLResponse:
    message = f"<p class='error'>{_esc(error)}</p>" if error else ""
    return _page(
        "Login",
        f"""
<form method="post" action="/login" class="login">
  {message}
  <label>Username <input name="username" autocomplete="username" required></label>
  <label>Password <input name="password" type="password" autocomplete="current-password" required></label>
  <button type="submit">Sign in</button>
</form>
""",
        user=None,
        status_code=status_code,
    )


def _page(title: str, body: str, *, user: str | None, status_code: int = 200) -> HTMLResponse:
    nav = (
        f"<form method='post' action='/logout'><span>{_esc(user)}</span><button>Sign out</button></form>"
        if user else ""
    )
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)} - Broker Panel</title>
<style>
:root {{ color-scheme: light; --bg:#f6f7f9; --ink:#18202a; --muted:#697483; --line:#d9dee7; --panel:#fff; --accent:#0d6efd; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif; }}
header {{ display:flex; align-items:center; justify-content:space-between; padding:14px 22px; background:#172033; color:white; }}
header h1 {{ font-size:18px; margin:0; font-weight:650; }}
main {{ max-width:1180px; margin:0 auto; padding:22px; }}
section, article {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; margin-bottom:16px; }}
h2 {{ font-size:16px; margin:0 0 12px; }}
button, input, select {{ font:inherit; min-height:34px; border-radius:6px; border:1px solid var(--line); padding:6px 9px; background:white; }}
button {{ background:#f3f5f8; cursor:pointer; }}
button[type=submit] {{ background:var(--accent); border-color:var(--accent); color:white; }}
a.button {{ display:inline-flex; align-items:center; min-height:34px; border-radius:6px; border:1px solid var(--line); padding:6px 9px; background:#f3f5f8; color:var(--ink); text-decoration:none; }}
textarea {{ width:100%; min-height:86px; resize:vertical; font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; border:1px solid var(--line); border-radius:6px; padding:8px; }}
table {{ width:100%; border-collapse:collapse; }}
td, th {{ border-top:1px solid var(--line); padding:8px; text-align:left; vertical-align:middle; }}
code {{ display:inline-block; max-width:100%; overflow-wrap:anywhere; background:#eef1f5; padding:2px 5px; border-radius:5px; }}
.toolbar, .inline-form, header form, .policy-head, .tool-head {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
.actions {{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; }}
.grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; background:transparent; border:0; padding:0; }}
.wide {{ grid-column:1 / -1; }}
.list {{ list-style:none; padding:0; margin:0; }}
.list li {{ display:flex; justify-content:space-between; gap:12px; border-top:1px solid var(--line); padding:8px 0; }}
.banner {{ background:#e9f8ef; border:1px solid #a9dfbc; padding:12px; border-radius:8px; margin-bottom:16px; }}
.error {{ background:#fff0f0; border:1px solid #efb3b3; padding:12px; border-radius:8px; color:#8c1f1f; }}
.login {{ max-width:380px; margin:60px auto; display:grid; gap:12px; }}
.login label {{ display:grid; gap:6px; }}
.risk {{ display:inline-block; min-width:84px; text-align:center; border-radius:999px; padding:2px 8px; font-size:12px; }}
.risk.read {{ background:#e8f5ff; color:#185d8f; }}
.risk.write {{ background:#fff6df; color:#7a5100; }}
.risk.destructive {{ background:#ffe8e8; color:#8c1f1f; }}
.tool-head {{ justify-content:space-between; }}
.tool-head p {{ margin:4px 0 0; color:var(--muted); max-width:720px; }}
@media (max-width:800px) {{ .grid {{ grid-template-columns:1fr; }} main {{ padding:12px; }} }}
</style>
<script>
function setAll(effect) {{
  document.querySelectorAll('select[name^="op__"]').forEach(s => s.value = effect);
}}
function setTool(tool, effect) {{
  document.querySelectorAll('select[name^="op__' + tool + '__"]').forEach(s => s.value = effect);
}}
function setToolRisk(tool, risk, effect) {{
  document.querySelectorAll('section.tool-policy').forEach(section => {{
    if (!section.querySelector('h2') || section.querySelector('h2').textContent !== tool) return;
    section.querySelectorAll('tr').forEach(row => {{
      const badge = row.querySelector('.risk');
      const select = row.querySelector('select');
      if (badge && select && badge.textContent === risk) select.value = effect;
    }});
  }});
}}
function setToolRecommended(tool) {{
  document.querySelectorAll('section.tool-policy').forEach(section => {{
    if (!section.querySelector('h2') || section.querySelector('h2').textContent !== tool) return;
    section.querySelectorAll('tr').forEach(row => {{
      const badge = row.querySelector('.risk');
      const select = row.querySelector('select');
      if (!badge || !select) return;
      select.value = badge.textContent === 'read' ? 'allow' : (badge.textContent === 'write' ? 'review' : 'deny');
    }});
  }});
}}
function setRecommended() {{
  document.querySelectorAll('section.tool-policy').forEach(section => {{
    const title = section.querySelector('h2');
    if (title) setToolRecommended(title.textContent);
  }});
}}
</script>
</head>
<body>
<header><h1>Broker Panel</h1>{nav}</header>
<main>{body}</main>
</body>
</html>""",
        status_code=status_code,
    )


def _current_user(request: Request) -> str | None:
    cfg = request.app.state.config
    return verify_session(request.cookies.get(SESSION_COOKIE), cfg.session_secret())


async def _form(request: Request) -> dict[str, str]:
    raw = (await request.body()).decode("utf-8")
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="broker-panel")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve")
    serve.add_argument("--bind", default=None)
    hash_cmd = sub.add_parser("hash-password")
    hash_cmd.add_argument("password")
    args = parser.parse_args(argv)

    if args.command == "hash-password":
        print(hash_password(args.password))
        return

    config = load_config()
    bind = args.bind or config.bind_addr
    host, raw_port = bind.rsplit(":", 1)
    uvicorn.run(create_app(config), host=host, port=int(raw_port))


if __name__ == "__main__":
    main(sys.argv[1:])
