"""Tests for the embed builder.

These verify color coding, field layout, argument redaction, and truncation.
discord.Embed objects are tested via .to_dict() for easy assertions.
"""

from __future__ import annotations

from discord_approver.embed import (
    COLOR_APPROVED,
    COLOR_GRAY,
    COLOR_PENDING,
    COLOR_REJECTED,
    _redact_arguments,
    build_approval_embed,
)
from discord_approver.models import Request, RequestStatus


class TestStatusColors:
    def test_pending_is_yellow(self, pending_request):
        embed = build_approval_embed(pending_request)
        assert embed.color.value == COLOR_PENDING

    def test_approved_is_green(self, approved_request):
        embed = build_approval_embed(approved_request)
        assert embed.color.value == COLOR_APPROVED

    def test_rejected_is_red(self, rejected_request):
        embed = build_approval_embed(rejected_request)
        assert embed.color.value == COLOR_REJECTED

    def test_expired_is_red(self, expired_request):
        embed = build_approval_embed(expired_request)
        assert embed.color.value == COLOR_REJECTED

    def test_completed_is_green(self, approved_request):
        completed = approved_request.model_copy(
            update={"status": RequestStatus.COMPLETED}
        )
        embed = build_approval_embed(completed)
        assert embed.color.value == COLOR_APPROVED

    def test_failed_is_red(self, approved_request):
        failed = approved_request.model_copy(
            update={"status": RequestStatus.FAILED}
        )
        embed = build_approval_embed(failed)
        assert embed.color.value == COLOR_REJECTED

    def test_denied_is_red(self, approved_request):
        denied = approved_request.model_copy(
            update={"status": RequestStatus.DENIED}
        )
        embed = build_approval_embed(denied)
        assert embed.color.value == COLOR_REJECTED


class TestEmbedLayout:
    def test_pending_title_has_approval_needed(self, pending_request):
        embed = build_approval_embed(pending_request)
        assert embed.title == "Approval needed: media.skip_track"

    def test_terminal_title_is_tool_op(self, approved_request):
        embed = build_approval_embed(approved_request)
        assert embed.title == "media.skip_track"

    def test_pending_has_no_description(self, pending_request):
        embed = build_approval_embed(pending_request)
        assert embed.description is None

    def test_terminal_has_status_description(self, approved_request):
        embed = build_approval_embed(approved_request)
        assert "Approved" in embed.description

    def test_inline_fields_present(self, pending_request):
        embed = build_approval_embed(pending_request)
        d = embed.to_dict()
        fields = d["fields"]
        caller_field = fields[0]
        risk_field = fields[1]
        assert caller_field["name"] == "Caller"
        assert caller_field["value"] == "agent.hermes"
        assert caller_field["inline"] is True
        assert risk_field["name"] == "Risk"

    def test_reason_field_present_when_set(self, pending_request):
        embed = build_approval_embed(pending_request)
        d = embed.to_dict()
        field_names = [f["name"] for f in d["fields"]]
        assert "Reason" in field_names

    def test_reason_field_absent_when_none(self, pending_request):
        no_reason = pending_request.model_copy(update={"reason": None})
        embed = build_approval_embed(no_reason)
        d = embed.to_dict()
        field_names = [f["name"] for f in d["fields"]]
        assert "Reason" not in field_names

    def test_arguments_field_present(self, pending_request):
        embed = build_approval_embed(pending_request)
        d = embed.to_dict()
        field_names = [f["name"] for f in d["fields"]]
        assert "Arguments" in field_names

    def test_arguments_field_absent_when_empty(self, pending_request):
        no_args = pending_request.model_copy(update={"arguments": {}})
        embed = build_approval_embed(no_args)
        d = embed.to_dict()
        field_names = [f["name"] for f in d["fields"]]
        assert "Arguments" not in field_names

    def test_decision_field_absent_for_pending(self, pending_request):
        embed = build_approval_embed(pending_request)
        d = embed.to_dict()
        field_names = [f["name"] for f in d["fields"]]
        assert "Decision" not in field_names

    def test_decision_field_present_for_approved(self, approved_request):
        embed = build_approval_embed(approved_request)
        d = embed.to_dict()
        field_names = [f["name"] for f in d["fields"]]
        assert "Decision" in field_names
        decision = next(f for f in d["fields"] if f["name"] == "Decision")
        assert "testuser" in decision["value"]

    def test_decision_field_for_expired(self, expired_request):
        embed = build_approval_embed(expired_request)
        d = embed.to_dict()
        decision = next(f for f in d["fields"] if f["name"] == "Decision")
        assert "Expired" in decision["value"]

    def test_footer_has_request_id(self, pending_request):
        embed = build_approval_embed(pending_request)
        assert "Request #1" in embed.footer.text

    def test_pending_footer_has_expires_in(self, pending_request):
        embed = build_approval_embed(pending_request)
        assert "Expires in" in embed.footer.text


class TestArgumentRedaction:
    def test_password_redacted(self):
        result = _redact_arguments({"password": "secret123", "name": "ok"})
        assert result["password"] == "**REDACTED**"
        assert result["name"] == "ok"

    def test_api_key_redacted(self):
        result = _redact_arguments({"api_key": "sk-abc"})
        assert result["api_key"] == "**REDACTED**"

    def test_token_redacted(self):
        result = _redact_arguments({"auth_token": "tok123"})
        assert result["auth_token"] == "**REDACTED**"

    def test_secret_redacted(self):
        result = _redact_arguments({"client_secret": "shh"})
        assert result["client_secret"] == "**REDACTED**"

    def test_authorization_redacted(self):
        result = _redact_arguments({"authorization": "Bearer xyz"})
        assert result["authorization"] == "**REDACTED**"

    def test_nested_redaction(self):
        result = _redact_arguments({"data": {"token": "nested_secret", "safe": "ok"}})
        assert result["data"]["token"] == "**REDACTED**"
        assert result["data"]["safe"] == "ok"

    def test_safe_fields_untouched(self):
        result = _redact_arguments({"device_id": "abc", "direction": "forward"})
        assert result == {"device_id": "abc", "direction": "forward"}

    def test_embed_redacts_sensitive_args(self, request_with_secrets):
        embed = build_approval_embed(request_with_secrets)
        d = embed.to_dict()
        args_field = next(f for f in d["fields"] if f["name"] == "Arguments")
        assert "secret123" not in args_field["value"]
        assert "sk-abc-456" not in args_field["value"]
        assert "tok-789" not in args_field["value"]
        assert "safe-value" in args_field["value"]
        assert "REDACTED" in args_field["value"]


class TestArgumentTruncation:
    def test_long_arguments_truncated(self, pending_request):
        big_args = {f"key_{i}": f"value_{i}" * 20 for i in range(50)}
        long_req = pending_request.model_copy(update={"arguments": big_args})
        embed = build_approval_embed(long_req)
        d = embed.to_dict()
        args_field = next(f for f in d["fields"] if f["name"] == "Arguments")
        assert "(truncated)" in args_field["value"]
        # Must stay within Discord's 1024-char field limit
        assert len(args_field["value"]) <= 1024
