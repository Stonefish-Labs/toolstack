"""Discord bot — the thin shell that wires Discord events to the reconciler.

Registers persistent views (button handlers that survive restarts),
modal handlers for Approve+Note, Reject, and Reject+Reason flows,
and starts the reconciler as a background task.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from discord_approver.broker_client import BrokerClient
from discord_approver.embed import build_approval_embed, build_approval_view
from discord_approver.models import Request
from discord_approver.reconciler import ApprovalUI, Reconciler, _TERMINAL_STATUS_VALUES
from discord_approver.state import MessageStore

if TYPE_CHECKING:
    from discord_approver.config import Settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Modals
# ---------------------------------------------------------------------------


class ApproveNoteModal(discord.ui.Modal, title="Approve with Note"):
    """Modal for the Approve+Note button. Note is optional."""

    note = discord.ui.TextInput(
        label="Note (optional)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
        placeholder="Add context for the audit log...",
    )

    def __init__(
        self,
        request_id: int,
        broker: BrokerClient,
        allowed_user_ids: frozenset[int],
        allowed_role_ids: frozenset[int],
    ) -> None:
        super().__init__()
        self._request_id = request_id
        self._broker = broker
        self._allowed_user_ids = allowed_user_ids
        self._allowed_role_ids = allowed_role_ids

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _is_allowed_user(
            interaction.user, self._allowed_user_ids, self._allowed_role_ids
        ):
            await _send_unauthorized(interaction)
            return
        approver = _approver_label(interaction.user)
        note_val = self.note.value.strip() or None
        logger.info(
            "approve+note request=%d approver=%s (%s) note=%s",
            self._request_id, approver, interaction.user.display_name, note_val,
        )
        try:
            updated = await self._broker.approve(self._request_id, approver, note_val)
            embed = build_approval_embed(updated)
            await interaction.response.edit_message(embed=embed, view=None)
        except Exception as e:
            logger.exception("approve failed for request %d", self._request_id)
            await interaction.response.send_message(
                f"❌ Approve failed: {e}", ephemeral=True
            )



class RejectReasonModal(discord.ui.Modal, title="Reject with Reason"):
    """Modal for the Reject+Reason button. Reason is REQUIRED."""

    reason = discord.ui.TextInput(
        label="Reason (required)",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
        placeholder="You must provide a reason for rejection.",
    )

    def __init__(
        self,
        request_id: int,
        broker: BrokerClient,
        allowed_user_ids: frozenset[int],
        allowed_role_ids: frozenset[int],
    ) -> None:
        super().__init__()
        self._request_id = request_id
        self._broker = broker
        self._allowed_user_ids = allowed_user_ids
        self._allowed_role_ids = allowed_role_ids

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _is_allowed_user(
            interaction.user, self._allowed_user_ids, self._allowed_role_ids
        ):
            await _send_unauthorized(interaction)
            return
        approver = _approver_label(interaction.user)
        reason_val = self.reason.value.strip()
        logger.info(
            "reject+reason request=%d approver=%s (%s) reason=%s",
            self._request_id, approver, interaction.user.display_name, reason_val,
        )
        try:
            updated = await self._broker.reject(self._request_id, approver, reason_val)
            embed = build_approval_embed(updated)
            await interaction.response.edit_message(embed=embed, view=None)
        except Exception as e:
            logger.exception("reject+reason failed for request %d", self._request_id)
            await interaction.response.send_message(
                f"❌ Reject failed: {e}", ephemeral=True
            )


# ---------------------------------------------------------------------------
# Discord ApprovalUI implementation
# ---------------------------------------------------------------------------


class DiscordApprovalUI:
    """Implements the ApprovalUI protocol using Discord embeds and buttons."""

    def __init__(self, channel: discord.TextChannel) -> None:
        self._channel = channel

    async def post_card(self, request: Request) -> int:
        embed = build_approval_embed(request)
        view = build_approval_view(request.id)
        msg = await self._channel.send(embed=embed, view=view)
        return msg.id

    async def edit_card(self, message_id: int, request: Request | None) -> None:
        try:
            msg = await self._channel.fetch_message(message_id)
        except discord.NotFound:
            logger.warning("message %d not found in channel, skipping edit", message_id)
            return

        if request is None:
            embed = discord.Embed(
                title="Request removed",
                description="This request no longer exists in the broker.",
                color=0x99AAB5,
            )
            await msg.edit(embed=embed, view=None)
        else:
            embed = build_approval_embed(request)
            await msg.edit(embed=embed, view=None)

    async def delete_card(self, message_id: int) -> None:
        try:
            msg = await self._channel.fetch_message(message_id)
            await msg.delete()
        except discord.NotFound:
            logger.warning("message %d already deleted, skipping", message_id)


# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------


def _parse_request_id(custom_id: str) -> int | None:
    """Extract request_id from a custom_id like 'approve:123'."""
    try:
        return int(custom_id.split(":", 1)[1])
    except (IndexError, ValueError):
        return None


def _approver_label(user: discord.abc.User) -> str:
    return f"{user.name} ({user.id})"


def _is_allowed_user(
    user: discord.abc.User,
    allowed_user_ids: frozenset[int],
    allowed_role_ids: frozenset[int],
) -> bool:
    if int(user.id) in allowed_user_ids:
        return True
    roles = getattr(user, "roles", []) or []
    return any(int(getattr(role, "id", 0)) in allowed_role_ids for role in roles)


async def _send_unauthorized(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        "You are not allowed to approve or reject broker requests.", ephemeral=True
    )


class ApproverBot(discord.Client):
    """The Discord bot that handles approval interactions."""

    def __init__(
        self,
        settings: Settings,
        store: MessageStore,
        broker: BrokerClient,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = False  # We don't read message content
        super().__init__(intents=intents)

        self._settings = settings
        self._store = store
        self._broker = broker
        self._reconciler: Reconciler | None = None
        self._channel: discord.TextChannel | None = None
        self._tree = app_commands.CommandTree(self)
        self._register_commands()

    async def on_ready(self) -> None:
        logger.info("logged in as %s (id=%s)", self.user, self.user.id if self.user else "?")

        channel = self.get_channel(self._settings.discord_channel_id)
        if channel is None:
            channel = await self.fetch_channel(self._settings.discord_channel_id)

        if not isinstance(channel, discord.TextChannel):
            logger.error("channel %s is not a text channel", self._settings.discord_channel_id)
            await self.close()
            return

        self._channel = channel
        ui = DiscordApprovalUI(channel)
        self._reconciler = Reconciler(
            broker=self._broker,
            store=self._store,
            ui=ui,
            poll_interval=self._settings.poll_interval,
            max_terminal_messages=self._settings.max_terminal_messages,
        )

        # Start reconciler as background task
        self.loop.create_task(self._reconciler.run_forever())
        logger.info("reconciler started, polling every %.1fs", self._settings.poll_interval)

        # Sync slash commands to this guild (instant, vs global which takes ~1hr)
        guild = channel.guild
        self._tree.copy_global_to(guild=guild)
        await self._tree.sync(guild=guild)
        # Clear any stale global registrations (from previous runs)
        self._tree.clear_commands(guild=None)
        await self._tree.sync()
        logger.info("slash commands synced to guild %s", guild.name)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Handle button clicks. Slash commands are dispatched by the tree."""
        if interaction.type != discord.InteractionType.component:
            return

        custom_id = interaction.data.get("custom_id", "") if interaction.data else ""
        if not custom_id:
            return

        request_id = _parse_request_id(custom_id)
        if request_id is None:
            return

        logger.info(
            "interaction: %s request=%s user=%s (%s)",
            custom_id.split(":")[0], request_id,
            interaction.user.name, interaction.user.display_name,
        )

        if custom_id.startswith("approve:"):
            if not self._is_allowed(interaction.user):
                logger.warning(
                    "unauthorized approve attempt request=%d user=%s (%s)",
                    request_id, interaction.user.name, interaction.user.id,
                )
                await _send_unauthorized(interaction)
                return
            await self._handle_approve(interaction, request_id)
        elif custom_id.startswith("approve_note:"):
            if not self._is_allowed(interaction.user):
                logger.warning(
                    "unauthorized approve+note attempt request=%d user=%s (%s)",
                    request_id, interaction.user.name, interaction.user.id,
                )
                await _send_unauthorized(interaction)
                return
            await interaction.response.send_modal(
                ApproveNoteModal(
                    request_id,
                    self._broker,
                    self._settings.allowed_user_ids,
                    self._settings.allowed_role_ids,
                )
            )
        elif custom_id.startswith("reject:"):
            if not self._is_allowed(interaction.user):
                logger.warning(
                    "unauthorized reject attempt request=%d user=%s (%s)",
                    request_id, interaction.user.name, interaction.user.id,
                )
                await _send_unauthorized(interaction)
                return
            await self._handle_reject(interaction, request_id)
        elif custom_id.startswith("reject_reason:"):
            if not self._is_allowed(interaction.user):
                logger.warning(
                    "unauthorized reject+reason attempt request=%d user=%s (%s)",
                    request_id, interaction.user.name, interaction.user.id,
                )
                await _send_unauthorized(interaction)
                return
            await interaction.response.send_modal(
                RejectReasonModal(
                    request_id,
                    self._broker,
                    self._settings.allowed_user_ids,
                    self._settings.allowed_role_ids,
                )
            )

    async def _handle_approve(
        self, interaction: discord.Interaction, request_id: int
    ) -> None:
        """One-click approve — no modal."""
        approver = _approver_label(interaction.user)
        logger.info(
            "approve request=%d approver=%s (%s)",
            request_id, approver, interaction.user.display_name,
        )
        try:
            updated = await self._broker.approve(request_id, approver, note=None)
            embed = build_approval_embed(updated)
            await interaction.response.edit_message(embed=embed, view=None)
            # Update store
            self._store.upsert(request_id, interaction.message.id, updated.status.value)
        except Exception as e:
            logger.exception("approve failed for request %d", request_id)
            await interaction.response.send_message(
                f"❌ Approve failed: {e}", ephemeral=True
            )

    async def _handle_reject(
        self, interaction: discord.Interaction, request_id: int
    ) -> None:
        """One-click reject — no modal, no reason."""
        approver = _approver_label(interaction.user)
        logger.info(
            "reject request=%d approver=%s (%s)",
            request_id, approver, interaction.user.display_name,
        )
        try:
            updated = await self._broker.reject(request_id, approver, reason=None)
            embed = build_approval_embed(updated)
            await interaction.response.edit_message(embed=embed, view=None)
            self._store.upsert(request_id, interaction.message.id, updated.status.value)
        except Exception as e:
            logger.exception("reject failed for request %d", request_id)
            await interaction.response.send_message(
                f"❌ Reject failed: {e}", ephemeral=True
            )

    def _is_allowed(self, user: discord.abc.User) -> bool:
        return _is_allowed_user(
            user, self._settings.allowed_user_ids, self._settings.allowed_role_ids
        )

    def _register_commands(self) -> None:
        """Register slash commands on the command tree."""

        @self._tree.command(
            name="clear",
            description="Delete all completed/closed approval messages from this channel",
        )
        async def clear_command(interaction: discord.Interaction) -> None:
            await self._handle_clear(interaction)

    async def _handle_clear(self, interaction: discord.Interaction) -> None:
        """Delete all bot messages from the channel except pending approvals.

        Scans channel history for messages sent by the bot, so it catches
        orphaned messages from previous sessions too.
        """
        if not self._is_allowed(interaction.user):
            logger.warning(
                "unauthorized clear attempt user=%s (%s)",
                interaction.user.name, interaction.user.id,
            )
            await _send_unauthorized(interaction)
            return

        await interaction.response.defer(ephemeral=True)

        if not self._channel:
            await interaction.followup.send("❌ Channel not available.", ephemeral=True)
            return

        # Build set of message IDs for currently-pending requests (keep these)
        stored = self._store.list_all()
        pending_msg_ids = {
            m.message_id for m in stored
            if m.last_status not in _TERMINAL_STATUS_VALUES
        }

        deleted = 0
        async for msg in self._channel.history(limit=200):
            if msg.author.id != self.user.id:
                continue
            if msg.id in pending_msg_ids:
                continue
            try:
                await msg.delete()
                deleted += 1
            except Exception:
                logger.exception("failed to delete message %d", msg.id)

        # Clean up store entries for terminal messages
        for m in stored:
            if m.last_status in _TERMINAL_STATUS_VALUES:
                self._store.delete(m.request_id)

        logger.info("/clear by %s: deleted %d messages", interaction.user.name, deleted)
        await interaction.followup.send(
            f"🧹 Cleared {deleted} message{'s' if deleted != 1 else ''}. Pending approvals untouched.",
            ephemeral=True,
        )


def build_bot(settings: Settings, store: MessageStore, broker: BrokerClient) -> ApproverBot:
    """Factory function to create the bot."""
    return ApproverBot(settings, store, broker)
