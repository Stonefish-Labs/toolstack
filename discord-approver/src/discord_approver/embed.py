"""Pure embed builder for Discord approval cards.

Builds discord.Embed objects from Request data. The function is "pure" in
the sense that it takes data in, returns an embed out, with no side effects.
discord.Embed is a plain object with .to_dict() for test assertions.
"""

from __future__ import annotations

import json
import re
import time

import discord

from discord_approver.models import Request, RequestStatus

# Color constants (Discord brand colors)
COLOR_PENDING = 0xFEE75C  # Yellow
COLOR_APPROVED = 0x57F287  # Green
COLOR_REJECTED = 0xED4245  # Red
COLOR_GRAY = 0x99AAB5  # Gray

# Fields whose values should be redacted in the embed (defense-in-depth)
_SENSITIVE_PATTERN = re.compile(
    r"password|token|secret|api_key|authorization", re.IGNORECASE
)

# Max length for argument JSON in embed fields (Discord limit is 1024)
_MAX_ARGS_LENGTH = 800


def _status_color(status: RequestStatus) -> int:
    match status:
        case RequestStatus.PENDING_REVIEW:
            return COLOR_PENDING
        case RequestStatus.APPROVED | RequestStatus.COMPLETED:
            return COLOR_APPROVED
        case (
            RequestStatus.REJECTED | RequestStatus.EXPIRED
            | RequestStatus.DENIED | RequestStatus.FAILED
        ):
            return COLOR_REJECTED
        case _:
            return COLOR_GRAY


def _redact_arguments(arguments: dict) -> dict:
    redacted = {}
    for key, value in arguments.items():
        if _SENSITIVE_PATTERN.search(key):
            redacted[key] = "**REDACTED**"
        elif isinstance(value, dict):
            redacted[key] = _redact_arguments(value)
        else:
            redacted[key] = value
    return redacted


def _format_arguments(arguments: dict) -> str:
    redacted = _redact_arguments(arguments)
    text = json.dumps(redacted, indent=2, default=str)
    if len(text) > _MAX_ARGS_LENGTH:
        text = text[:_MAX_ARGS_LENGTH] + "\n... (truncated)"
    return f"```json\n{text}\n```"


def _relative_time(unix_ts: int | None, *, future: bool = False) -> str:
    if unix_ts is None:
        return "unknown"
    now = int(time.time())
    diff = (unix_ts - now) if future else (now - unix_ts)
    if diff < 0:
        return "now" if future else "just now"
    hours, remainder = divmod(diff, 3600)
    minutes = remainder // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    elif minutes > 0:
        return f"{minutes}m"
    return "now"


def _status_emoji(status: RequestStatus) -> str:
    match status:
        case RequestStatus.PENDING_REVIEW: return "⏳"
        case RequestStatus.APPROVED: return "✅"
        case RequestStatus.COMPLETED: return "✅"
        case RequestStatus.REJECTED: return "❌"
        case RequestStatus.EXPIRED: return "⏰"
        case RequestStatus.DENIED: return "🚫"
        case RequestStatus.FAILED: return "💥"
        case _: return "ℹ️"


def _build_decision_text(request: Request) -> str | None:
    match request.status:
        case RequestStatus.APPROVED:
            t = f"Approved by {request.approver or 'unknown'}"
            return f"{t}: {request.decision_note}" if request.decision_note else t
        case RequestStatus.COMPLETED:
            t = f"Approved by {request.approver or 'unknown'} · Completed"
            return f"{t}\nNote: {request.decision_note}" if request.decision_note else t
        case RequestStatus.REJECTED:
            t = f"Rejected by {request.approver or 'unknown'}"
            return f"{t}: {request.decision_note}" if request.decision_note else t
        case RequestStatus.EXPIRED:
            return "Expired (no decision within timeout)"
        case RequestStatus.DENIED:
            return "Policy denied"
        case RequestStatus.FAILED:
            t = "Execution failed"
            return f"{t}: {request.decision_note}" if request.decision_note else t
        case _:
            return None


def build_approval_embed(request: Request) -> discord.Embed:
    """Build a Discord embed for an approval card.

    Layout per design/30-approver-discord.md:
    - Title: "Approval needed: {tool}.{op}" for pending; "{tool}.{op}" for terminal
    - Color: status-based
    - Fields: Caller, Profile, Risk (inline); Reason, Arguments, Decision (full width)
    - Footer: "Request #{id}" + relative time
    """
    is_pending = request.status == RequestStatus.PENDING_REVIEW
    tool_op = f"{request.tool}.{request.op}"

    if is_pending:
        title = f"Approval needed: {tool_op}"
        description = None
    else:
        emoji = _status_emoji(request.status)
        title = tool_op
        description = f"{emoji} **{request.status.value.replace('_', ' ').title()}**"

    embed = discord.Embed(title=title, description=description, color=_status_color(request.status))

    embed.add_field(name="Caller", value=request.caller, inline=True)
    embed.add_field(name="Profile", value=request.profile, inline=True)

    risk_emoji = {"read": "🟢", "write": "🟡", "destructive": "🔴"}.get(request.risk, "⚪")
    embed.add_field(name="Risk", value=f"{risk_emoji} {request.risk}", inline=True)

    if request.reason:
        embed.add_field(name="Reason", value=request.reason, inline=False)

    if request.arguments:
        embed.add_field(name="Arguments", value=_format_arguments(request.arguments), inline=False)

    if not is_pending:
        decision = _build_decision_text(request)
        if decision:
            embed.add_field(name="Decision", value=decision, inline=False)

    # Footer
    if is_pending and request.expires_at:
        footer = f"Request #{request.id} · Expires in {_relative_time(request.expires_at, future=True)}"
    elif not is_pending:
        footer = f"Request #{request.id} · {request.status.value.replace('_', ' ').title()}"
    else:
        footer = f"Request #{request.id}"
    embed.set_footer(text=footer)

    return embed


def build_approval_view(request_id: int) -> discord.ui.View:
    """Build the 4-button view for an approval card.

    Uses persistent custom_ids so button handlers survive bot restarts.
    """
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(style=discord.ButtonStyle.success, label="Approve", custom_id=f"approve:{request_id}"))
    view.add_item(discord.ui.Button(style=discord.ButtonStyle.success, label="Approve+Note", custom_id=f"approve_note:{request_id}"))
    view.add_item(discord.ui.Button(style=discord.ButtonStyle.danger, label="Reject", custom_id=f"reject:{request_id}"))
    view.add_item(discord.ui.Button(style=discord.ButtonStyle.danger, label="Reject+Reason", custom_id=f"reject_reason:{request_id}"))
    return view
