"""
Mack Bot — Admin Panel Cog
Contains:
  - GroupControlPanelView: 3-row persistent panel in every group channel
  - /panel command for admin overview
  - /config, /viewconfig, /unban, /banlist commands
  - Modals for Edit Match, Move Team, Punish Team
"""

import datetime
import asyncio
import discord
from discord.ext import commands
from discord import app_commands, ui

from config import get_today_event_id, Theme, TIMEZONE_OFFSET, DEFAULT_LOCK_MINUTES, get_rank_emoji, DEFAULT_RESERVED_SLOTS
from utils.embeds import make_embed, error_embed, success_embed, build_roster_embed
from utils.permissions import grant_group_access, revoke_group_access
from models import group as group_model, registration as reg_model, punishment
from database import get_config, set_config, get_channel_config, set_channel_config
from utils.updater import update_group_roster, update_registration_board


# ═══════════════════ HELPERS ═══════════════════


def is_admin(member: discord.Member) -> bool:
    """Check if a member has admin permissions."""
    return member.guild_permissions.administrator


# ═══════════════════ MODALS ═══════════════════

class EditMatchModal(ui.Modal, title="✏️ Edit Match Details"):
    """Modal for editing a group's match IDP, start time, and map."""

    match_num = ui.TextInput(label="Match Number (1 or 2)", placeholder="1", max_length=1)
    idp_time = ui.TextInput(label="IDP Time", placeholder="2:00 PM", required=False)
    start_time = ui.TextInput(label="Start Time", placeholder="2:10 PM", required=False)
    map_name = ui.TextInput(label="Map", placeholder="Erangel", required=False)

    def __init__(self, event_id: str, group_id: str):
        super().__init__()
        self.event_id = event_id
        self.group_id = group_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            match_n = int(self.match_num.value.strip())
            if match_n not in (1, 2):
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid", "Match number must be 1 or 2."),
                ephemeral=True
            )
            return

        group_doc = group_model.get_group(self.event_id, self.group_id)
        if not group_doc:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Group `{self.group_id}` not found for today."),
                ephemeral=True
            )
            return

        details = {}
        if self.idp_time.value.strip():
            details["idp"] = self.idp_time.value.strip()
        if self.start_time.value.strip():
            details["start"] = self.start_time.value.strip()
        if self.map_name.value.strip():
            details["map"] = self.map_name.value.strip()

        if not details:
            await interaction.response.send_message(
                embed=error_embed("❌ Nothing Changed", "Fill in at least one field to update."),
                ephemeral=True
            )
            return

        group_model.update_match_details(self.event_id, self.group_id, match_n, details)

        updates = "\n".join([f"  ◆ **{k}:** `{v}`" for k, v in details.items()])
        await interaction.response.send_message(
            embed=success_embed(
                f"✅ Match {match_n} Updated — Group {self.group_id}",
                f"{Theme.SEP}\n\n{updates}\n\n{Theme.SEP}"
            ),
            ephemeral=True
        )


class MoveTeamModal(ui.Modal, title="🔀 Move Team"):
    """Modal for moving a team between groups (admin override)."""

    user_id_input = ui.TextInput(label="Team Owner's User ID", placeholder="123456789012345678")
    target_group = ui.TextInput(label="Target Group ID", placeholder="G0005")

    async def on_submit(self, interaction: discord.Interaction):
        event_id = get_today_event_id()
        owner_id = self.user_id_input.value.strip()
        new_gid = self.target_group.value.strip().upper()

        reg = reg_model.get_registration(owner_id, event_id)
        if not reg:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"No registration found for user `{owner_id}` today."),
                ephemeral=True
            )
            return

        old_gid = reg.get("group_id")
        new_group = group_model.get_group(event_id, new_gid)
        if not new_group:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Group `{new_gid}` not found."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Atomic move (admin override — ignores lock)
        from database import groups as groups_collection
        from pymongo import ReturnDocument
        new_group_doc = groups_collection.find_one_and_update(
            {"event_id": event_id, "group_id": new_gid},
            {"$inc": {"current_count": 1}},
            return_document=ReturnDocument.AFTER
        )
        group_model.release_slot(event_id, old_gid)
        reg_model.move_registration(owner_id, event_id, new_gid, new_group_doc.get("current_count"))

        # Swap roles
        guild = interaction.guild
        old_group_doc = group_model.get_group(event_id, old_gid)
        new_group_doc = group_model.get_group(event_id, new_gid)

        old_role = guild.get_role(old_group_doc.get("role_id")) if old_group_doc else None
        new_role = guild.get_role(new_group_doc.get("role_id")) if new_group_doc else None

        teammate_ids = reg.get("teammate_ids", [owner_id])
        for tid in teammate_ids:
            member = guild.get_member(int(tid))
            if member:
                if old_role:
                    try: await member.remove_roles(old_role)
                    except Exception: pass
                if new_role:
                    try: await member.add_roles(new_role)
                    except Exception: pass

        team_name = reg.get("team_name", "???")

        # Refresh rosters and board
        await update_group_roster(guild, event_id, old_gid)
        await update_group_roster(guild, event_id, new_gid)
        await update_registration_board(guild, event_id)

        await interaction.followup.send(
            embed=success_embed(
                "✅ Team Moved",
                f"**{team_name}** moved from `{old_gid}` → `{new_gid}`"
            ),
            ephemeral=True
        )


class PunishModal(ui.Modal, title="🔨 Punish Team"):
    """Modal for banning a team owner."""

    user_id_input = ui.TextInput(label="User ID to Ban", placeholder="123456789012345678")
    days_input = ui.TextInput(label="Ban Duration (days, 0 = permanent)", placeholder="2", max_length=3)
    reason_input = ui.TextInput(
        label="Reason",
        placeholder="e.g. Wasted slot — no-show without cancelling",
        style=discord.TextStyle.paragraph,
        max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction):
        owner_id = self.user_id_input.value.strip()

        try:
            days = int(self.days_input.value.strip())
            if days < 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid", "Days must be 0 or positive."),
                ephemeral=True
            )
            return

        reason = self.reason_input.value.strip() or "No reason provided"
        admin_id = str(interaction.user.id)

        guild = interaction.guild
        member = guild.get_member(int(owner_id))
        username = str(member) if member else owner_id

        punishment.ban_user(owner_id, username, reason, days, admin_id)

        # Cancel today's registration if exists
        event_id = get_today_event_id()
        reg = reg_model.get_registration(owner_id, event_id)
        if reg:
            reg_model.cancel_registration(owner_id, event_id)
            group_model.release_slot(event_id, reg["group_id"])
            group_doc = group_model.get_group(event_id, reg["group_id"])
            if group_doc and member:
                role = guild.get_role(group_doc.get("role_id"))
                if role:
                    try: await member.remove_roles(role)
                    except: pass

        duration_str = "permanently" if days == 0 else f"for {days} days"
        embed = make_embed(
            "🔨 Player Banned",
            f"{Theme.SEP}\n\n"
            f"👤 **Player:** <@{owner_id}> (`{username}`)\n"
            f"⏱️ **Duration:** {duration_str}\n"
            f"📝 **Reason:** {reason}\n"
            f"**Banned by:** {interaction.user.mention}\n\n{Theme.SEP}",
            Theme.ERROR
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

        # Log
        log_ch_id = get_channel_config("admin_log")
        if log_ch_id:
            log_ch = guild.get_channel(log_ch_id)
            if log_ch:
                await log_ch.send(embed=embed)

        # DM the user
        if member:
            try:
                await member.send(embed=make_embed(
                    "🔨 You Have Been Banned",
                    f"{Theme.SEP}\n\n"
                    f"You have been banned from scrims {duration_str}.\n"
                    f"📝 **Reason:** {reason}\n\n"
                    f"*Contact an admin if you believe this is an error.*\n\n{Theme.SEP}",
                    Theme.ERROR
                ))
            except: pass


# ═══════════════════ GROUP CONTROL PANEL (Per-Channel) ═══════════════════

class GroupControlPanelView(ui.View):
    """
    Persistent 3-row control panel placed in every group channel.

    Row 1 (Admin Only):
      ⏰ Match Reminder | 📤 Publish Slot List
    Row 2 (Teams & Admins):
      🛠️ Manage Matches | 🔨 Punish Team (Admin Only)
    Row 3 (Admin Only):
      🌟 Qualified Teams
    """

    def __init__(self, event_id: str = None, group_id: str = None):
        super().__init__(timeout=None)
        self.event_id = event_id
        self.group_id = group_id

    # ── Row 1: Admin Only ──

    @ui.button(label="⏰ Match Reminder", style=discord.ButtonStyle.primary, row=0,
               custom_id="gcp_reminder")
    async def reminder_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("⛔ Access Denied", "Only admins can send match reminders."),
                ephemeral=True
            )
            return

        event_id, group_id = self._resolve_context(interaction)
        if not event_id or not group_id:
            await interaction.response.send_message(embed=error_embed("❌ Error", "Group context lost."), ephemeral=True)
            return

        cog = interaction.client.get_cog("RemindersCog")
        if cog and group_id:
            await cog.remind_group.callback(cog, interaction, group_id=group_id)
        else:
            await interaction.response.send_message(
                embed=error_embed("❌ Error", "Reminders cog not loaded or group not found."),
                ephemeral=True
            )

    @ui.button(label="📤 Publish Slot List", style=discord.ButtonStyle.secondary, row=0,
               custom_id="gcp_slotlist")
    async def slot_list_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("⛔ Access Denied", "Only admins can publish slot lists."),
                ephemeral=True
            )
            return

        event_id, group_id = self._resolve_context(interaction)
        if not event_id or not group_id:
            await interaction.response.send_message(embed=error_embed("❌ Error", "Group context lost."), ephemeral=True)
            return

        cog = interaction.client.get_cog("RemindersCog")
        if cog and group_id:
            await cog.publish_slot_list.callback(cog, interaction, group_id=group_id)
        else:
            await interaction.response.send_message(
                embed=error_embed("❌ Error", "Reminders cog not loaded or group not found."),
                ephemeral=True
            )

    # ── Row 2: Teams & Admins ──

    @ui.button(label="🛠️ Manage Matches", style=discord.ButtonStyle.secondary, row=1,
               custom_id="gcp_manage")
    async def manage_btn(self, interaction: discord.Interaction, button: ui.Button):
        """
        For teams: shows Cancel Slot / Change Schedule
        For admins: shows Edit Match / Move Team
        """
        event_id, group_id = self._resolve_context(interaction)
        if not event_id or not group_id:
            await interaction.response.send_message(embed=error_embed("❌ Error", "Group context lost."), ephemeral=True)
            return

        if is_admin(interaction.user):
            # Admin sub-menu
            view = AdminManageSubView(event_id, group_id)
            await interaction.response.send_message(
                embed=make_embed(
                    "🔧 Admin — Manage Matches",
                    f"{Theme.SEP}\n\n"
                    f"**✏️ Edit Match** — Change IDP, start time, or map\n"
                    f"**🔀 Move Team** — Admin override to move a team\n\n{Theme.SEP}",
                    Theme.ACCENT
                ),
                view=view, ephemeral=True
            )
        else:
            # Team sub-menu (Cancel / Change Schedule)
            owner_id = str(interaction.user.id)
            reg = reg_model.get_registration(owner_id, event_id)

            if not reg or reg.get("group_id") != group_id:
                await interaction.response.send_message(
                    embed=error_embed("❌ Not Found", "You don't have a registration in this group."),
                    ephemeral=True
                )
                return

            view = TeamManageSubView(event_id, group_id)
            await interaction.response.send_message(
                embed=make_embed(
                    "🛠️ Manage Your Match",
                    f"{Theme.SEP}\n\n"
                    f"**❌ Cancel Slot** — Remove your team from this group\n"
                    f"**🔄 Change Schedule** — Move to a different group\n\n{Theme.SEP}",
                    Theme.ACCENT
                ),
                view=view, ephemeral=True
            )

    @ui.button(label="🔨 Punish Team", style=discord.ButtonStyle.danger, row=1,
               custom_id="gcp_punish")
    async def punish_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("⛔ Access Denied", "Only admins can punish teams."),
                ephemeral=True
            )
            return
        await interaction.response.send_modal(PunishModal())

    # ── Row 3: Admin Only ──

    @ui.button(label="🌟 Qualified Teams", style=discord.ButtonStyle.success, row=2,
               custom_id="gcp_qualified")
    async def qualified_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("⛔ Access Denied", "Only admins can view qualified teams."),
                ephemeral=True
            )
            return

        event_id, _ = self._resolve_context(interaction)
        if not event_id:
            from config import get_today_event_id
            event_id = get_today_event_id()
        from database import match_results as results_collection
        results = list(results_collection.find({"event_id": event_id}))

        if not results:
            await interaction.response.send_message(
                embed=error_embed("❌ No Standings", "No match results recorded yet today."),
                ephemeral=True
            )
            return

        team_totals = {}
        for r in results:
            tk = r.get("team_key") or r.get("team_name", "").strip().lower()
            if not tk:
                continue
            if tk not in team_totals:
                team_totals[tk] = {
                    "team_name": r.get("team_name", "?"),
                    "total_kills": 0,
                    "total_points": 0,
                    "matches_played": 0,
                }
            team_totals[tk]["total_kills"] += r.get("kills", 0)
            team_totals[tk]["total_points"] += r.get("total_points", 0)
            team_totals[tk]["matches_played"] += 1

        sorted_teams = sorted(team_totals.values(), key=lambda x: (x["total_points"], x["total_kills"]), reverse=True)

        lines = []
        for rank, t in enumerate(sorted_teams[:16], 1):
            medal = get_rank_emoji(rank)
            lines.append(f"{medal} **{t['team_name']}** ─ `{t['total_points']}` pts │ 💀 `{t['total_kills']}` kills")

        embed = make_embed(
            "🏆 Top Qualifying Teams",
            f"{Theme.SEP}\n\n"
            f"Here are the top **{len(lines)}** teams qualifying based on current standings:\n\n"
            + "\n".join(lines) + f"\n\n{Theme.SEP}",
            Theme.GOLD
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    def _resolve_context(self, interaction):
        """Resolve the true event_id and group_id from the channel context dynamically."""
        if self.event_id and self.group_id:
            return self.event_id, self.group_id

        from database import groups as groups_collection
        doc = groups_collection.find_one({
            "channel_id": interaction.channel.id,
            "archived": {"$ne": True}
        })
        if doc:
            return doc.get("event_id"), doc.get("group_id")
        return None, None


# ═══════════════════ ADMIN MANAGE SUB-VIEW ═══════════════════

class AdminManageSubView(ui.View):
    """Sub-view for admin: Edit Match and Move Team buttons."""

    def __init__(self, event_id, group_id):
        super().__init__(timeout=60)
        self.event_id = event_id
        self.group_id = group_id

    @ui.button(label="✏️ Edit Match Details", style=discord.ButtonStyle.primary)
    async def edit_match(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(EditMatchModal(self.event_id, self.group_id))

    @ui.button(label="🔀 Move Team", style=discord.ButtonStyle.secondary)
    async def move_team(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(MoveTeamModal())


# ═══════════════════ TEAM MANAGE SUB-VIEW ═══════════════════

class TeamManageSubView(ui.View):
    """Sub-view for teams: Cancel Slot and Change Schedule."""

    def __init__(self, event_id, group_id):
        super().__init__(timeout=60)
        self.event_id = event_id
        self.group_id = group_id

    @ui.button(label="❌ Cancel Slot", style=discord.ButtonStyle.danger)
    async def cancel_slot(self, interaction: discord.Interaction, button: ui.Button):
        owner_id = str(interaction.user.id)
        event_id = self.event_id or get_today_event_id()

        # Check if the group is locked
        group_doc = group_model.get_group(event_id, self.group_id)
        if not group_doc:
            await interaction.response.send_message(
                embed=error_embed("❌ Error", "Group not found."),
                ephemeral=True
            )
            return

        if group_doc.get("locked", False):
            lock_min = int(get_config("lock_minutes", DEFAULT_LOCK_MINUTES))
            await interaction.response.send_message(
                embed=error_embed(
                    "⛔ Locked",
                    f"{Theme.SEP}\n\n"
                    f"Cancellation is **locked** — less than {lock_min} minutes before match.\n"
                    f"Contact an admin if you need to withdraw.\n\n{Theme.SEP}"
                ),
                ephemeral=True
            )
            return

        # Verify ownership
        reg = reg_model.get_registration(owner_id, event_id)
        if not reg or reg.get("group_id") != self.group_id:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", "You don't have a registration in this group."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Cancel the registration
        cancelled = reg_model.cancel_registration(owner_id, event_id)
        if not cancelled:
            await interaction.followup.send(
                embed=error_embed("❌ Error", "Could not cancel your registration."),
                ephemeral=True
            )
            return

        # Release the group slot
        group_model.release_slot(event_id, self.group_id)

        # Remove group role
        guild = interaction.guild
        role = guild.get_role(group_doc.get("role_id"))
        if role:
            teammate_ids = cancelled.get("teammate_ids", [])
            for tid in teammate_ids:
                member = guild.get_member(int(tid))
                if member and role:
                    await revoke_group_access(member, role)

        # Refresh roster and board
        await update_group_roster(interaction.guild, event_id, self.group_id)
        await update_registration_board(interaction.guild, event_id)

        team_name = cancelled.get("team_name", "your team")
        await interaction.followup.send(
            embed=success_embed(
                "✅ Slot Cancelled",
                f"{Theme.SEP}\n\n"
                f"Team **{team_name}** has been removed from Group **{self.group_id}**.\n\n{Theme.SEP}"
            ),
            ephemeral=True
        )

    @ui.button(label="🔄 Change Schedule", style=discord.ButtonStyle.secondary)
    async def change_schedule(self, interaction: discord.Interaction, button: ui.Button):
        owner_id = str(interaction.user.id)
        event_id = self.event_id or get_today_event_id()

        # Check if locked
        group_doc = group_model.get_group(event_id, self.group_id)
        if not group_doc:
            await interaction.response.send_message(
                embed=error_embed("❌ Error", "Group not found."),
                ephemeral=True
            )
            return

        if group_doc.get("locked", False):
            await interaction.response.send_message(
                embed=error_embed("⛔ Locked", "Schedule changes are locked before match start."),
                ephemeral=True
            )
            return

        reg = reg_model.get_registration(owner_id, event_id)
        if not reg or reg.get("group_id") != self.group_id:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", "You don't have a registration in this group."),
                ephemeral=True
            )
            return

        # Show open groups
        open_groups = group_model.get_open_groups(event_id)
        open_groups = [g for g in open_groups if g["group_id"] != self.group_id]

        if not open_groups:
            await interaction.response.send_message(
                embed=error_embed("❌ No Available Groups", "All other groups are full or locked."),
                ephemeral=True
            )
            return

        options = []
        for g in open_groups[:25]:
            gid = g["group_id"]
            reserved = g.get("reserved_slots", 0)
            pub_count = max(0, g["current_count"] - reserved)
            pub_cap = g["capacity"] - reserved
            m1 = g.get("match1", {}).get("start", "TBD")
            options.append(
                discord.SelectOption(
                    label=f"Group {gid}",
                    description=f"{pub_count}/{pub_cap} filled │ M1: {m1}",
                    value=gid,
                    emoji="📍"
                )
            )

        view = ChangeGroupSelectView(event_id, self.group_id, options)
        await interaction.response.send_message(
            embed=make_embed(
                "🔄 Change Group",
                f"{Theme.SEP}\n\nSelect a new group from the dropdown below.\n\n{Theme.SEP}",
                Theme.ACCENT
            ),
            view=view,
            ephemeral=True
        )

    async def _refresh_board(self, guild, event_id):
        """Refresh the registration board after a cancel."""
        from utils.embeds import build_registration_board_embed
        reg_channel_id = get_channel_config("register")
        if not reg_channel_id:
            return

        channel = guild.get_channel(reg_channel_id)
        if not channel:
            return

        all_groups = group_model.get_all_groups(event_id)
        embed = build_registration_board_embed(all_groups)

        slot_msg_id = get_config("slot_message_id")
        if slot_msg_id:
            try:
                msg = await channel.fetch_message(slot_msg_id)
                await msg.edit(embed=embed)
            except discord.NotFound:
                pass


# ═══════════════════ CHANGE GROUP DROPDOWN ═══════════════════

class ChangeGroupSelectDropdown(ui.Select):
    """Dropdown for picking a new group (team self-service)."""

    def __init__(self, event_id, current_group_id, options):
        self.event_id = event_id
        self.current_group_id = current_group_id
        super().__init__(
            placeholder="📍 Select new group…",
            min_values=1, max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        new_group_id = self.values[0]
        owner_id = str(interaction.user.id)
        event_id = self.event_id

        await interaction.response.defer(ephemeral=True)

        # Atomic move
        old_group, new_group = group_model.move_slot(event_id, self.current_group_id, new_group_id)
        if not new_group:
            await interaction.followup.send(
                embed=error_embed("❌ Move Failed", "The target group is now full."),
                ephemeral=True
            )
            return

        # Update registration
        reg_model.move_registration(owner_id, event_id, new_group_id, new_group["current_count"])

        # Swap roles
        guild = interaction.guild
        old_role = guild.get_role(old_group.get("role_id")) if old_group else None
        new_role = guild.get_role(new_group.get("role_id"))

        reg = reg_model.get_registration(owner_id, event_id)
        teammate_ids = reg.get("teammate_ids", []) if reg else [owner_id]

        for tid in teammate_ids:
            member = guild.get_member(int(tid))
            if member:
                if old_role:
                    await revoke_group_access(member, old_role)
                if new_role:
                    await grant_group_access(member, new_role)

        # Refresh both rosters and board
        await update_group_roster(interaction.guild, event_id, self.current_group_id)
        await update_group_roster(interaction.guild, event_id, new_group_id)
        await update_registration_board(interaction.guild, event_id)

        await interaction.followup.send(
            embed=success_embed(
                "✅ Group Changed",
                f"{Theme.SEP}\n\n"
                f"Moved from **{self.current_group_id}** → **{new_group_id}**\n\n{Theme.SEP}"
            ),
            ephemeral=True
        )


class ChangeGroupSelectView(ui.View):
    def __init__(self, event_id, current_group_id, options):
        super().__init__(timeout=60)
        self.add_item(ChangeGroupSelectDropdown(event_id, current_group_id, options))


# ═══════════════════ ADMIN PANEL COG ═══════════════════

class AdminPanelCog(commands.Cog):
    """Admin panel with /panel and /config commands."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        """Register the persistent group control panel view."""
        self.bot.add_view(GroupControlPanelView())

    @app_commands.command(name="panel", description="[Admin] Open the admin control panel")
    @app_commands.checks.has_permissions(administrator=True)
    async def panel(self, interaction: discord.Interaction):
        event_id = get_today_event_id()
        all_groups = group_model.get_all_groups(event_id)
        total_regs = reg_model.count_registrations(event_id)
        total_capacity = sum(g.get("capacity", 0) for g in all_groups)
        locked_count = sum(1 for g in all_groups if g.get("locked"))

        embed = make_embed(
            "⚙️ Admin Control Panel",
            f"╭── 📊 **Today's Overview** ──╮\n"
            f"│  📅 **Event:** `{event_id}`\n"
            f"│  👥 **Groups:** `{len(all_groups)}`\n"
            f"│  📋 **Registrations:** `{total_regs}/{total_capacity}`\n"
            f"│  🔒 **Locked:** `{locked_count}/{len(all_groups)}`\n"
            f"│  ▓ **Roster Fill:** {Theme.bar(total_regs, total_capacity, 14)}\n"
            f"╰────────────────────────────╯\n\n"
            f"Use the buttons below to manage today's scrims.",
            Theme.PREMIUM,
            f"Admin: {interaction.user.display_name} │ Mack Bot 2027"
        )

        # Use the group-agnostic admin panel
        view = AdminPanelQuickView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ─────────────── CONFIG COMMANDS ───────────────

    # ── Toggle settings that accept enable/disable ──
    TOGGLE_SETTINGS = {
        "auto_group_generation", "auto_registration_open", "midnight_reset",
        "match_reminders", "waiting_list", "team_memory", "sync_commands_on_startup",
    }
    # ── String settings (no integer parsing) ──
    STRING_SETTINGS = {
        "default_category_name", "event_name", "event_mode",
        "match_format", "group_naming_pattern",
    }
    # ── Human-readable descriptions for each toggle ──
    TOGGLE_DESCRIPTIONS = {
        "auto_group_generation": "Auto-create groups at midnight",
        "auto_registration_open": "Auto-open registration at scheduled time",
        "midnight_reset": "Run full midnight cleanup & reset cycle",
        "match_reminders": "Send match reminder DMs to teams",
        "waiting_list": "Enable waiting list when groups are full",
        "team_memory": "Remember team profiles for 30 days",
        "sync_commands_on_startup": "Sync slash commands on next bot restart",
    }

    @app_commands.command(name="config", description="[Admin] Configure bot channels and settings")
    @app_commands.describe(
        setting="What to configure",
        channel="Channel to set (for channel settings)",
        value="Value to set (for non-channel settings — use enable/disable for toggles)"
    )
    @app_commands.choices(setting=[
        # ── Channel settings ──
        app_commands.Choice(name="📢 register_channel", value="register"),
        app_commands.Choice(name="📢 admin_channel", value="admin"),
        app_commands.Choice(name="📢 admin_log_channel", value="admin_log"),
        app_commands.Choice(name="📢 leaderboard_channel", value="leaderboard"),
        app_commands.Choice(name="📢 registered_teams_channel", value="registered_teams"),
        # ── Toggle settings (enable/disable) ──
        app_commands.Choice(name="🔘 auto_group_generation", value="auto_group_generation"),
        app_commands.Choice(name="🔘 auto_registration_open", value="auto_registration_open"),
        app_commands.Choice(name="🔘 midnight_reset", value="midnight_reset"),
        app_commands.Choice(name="🔘 match_reminders", value="match_reminders"),
        app_commands.Choice(name="🔘 waiting_list", value="waiting_list"),
        app_commands.Choice(name="🔘 team_memory", value="team_memory"),
        app_commands.Choice(name="🔘 sync_commands_on_startup", value="sync_commands_on_startup"),
        # ── Numeric settings ──
        app_commands.Choice(name="🔢 default_group_count", value="default_group_count"),
        app_commands.Choice(name="🔢 default_group_capacity", value="default_group_capacity"),
        app_commands.Choice(name="🔢 reminder_lead_minutes", value="reminder_lead_minutes"),
        app_commands.Choice(name="🔢 lock_minutes", value="lock_minutes"),
        app_commands.Choice(name="🔢 registration_open_hour", value="registration_open_hour"),
        app_commands.Choice(name="🔢 registration_open_minute", value="registration_open_minute"),
        app_commands.Choice(name="🔢 default_reserved_slots", value="default_reserved_slots"),
        # ── String settings ──
        app_commands.Choice(name="✏️ default_category_name", value="default_category_name"),
        app_commands.Choice(name="✏️ event_name", value="event_name"),
        app_commands.Choice(name="✏️ event_mode", value="event_mode"),
        app_commands.Choice(name="✏️ match_format", value="match_format"),
        app_commands.Choice(name="✏️ group_naming_pattern", value="group_naming_pattern"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def config_cmd(
        self,
        interaction: discord.Interaction,
        setting: str,
        channel: discord.TextChannel = None,
        value: str = None
    ):
        # ── Channel settings ──
        if setting in ("register", "admin", "admin_log", "leaderboard", "registered_teams"):
            if not channel:
                current = get_channel_config(setting)
                if current:
                    await interaction.response.send_message(
                        embed=make_embed("📋 Current Config", f"**{setting}_channel:** <#{current}>", Theme.INFO),
                        ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        embed=make_embed(
                            "📋 Not Set",
                            f"**{setting}_channel** is not configured.\nUse `/config {setting} #channel` to set it.",
                            Theme.WARNING
                        ),
                        ephemeral=True
                    )
                return

            set_channel_config(setting, channel.id)
            await interaction.response.send_message(
                embed=success_embed("✅ Config Updated", f"**{setting}_channel** set to {channel.mention}"),
                ephemeral=True
            )
            return

        # ── Toggle settings (enable/disable) ──
        if setting in self.TOGGLE_SETTINGS:
            desc = self.TOGGLE_DESCRIPTIONS.get(setting, setting)
            if not value:
                current = get_config(setting, True)
                status = "🟢 Enabled" if current else "🔴 Disabled"
                await interaction.response.send_message(
                    embed=make_embed(
                        f"🔘 {setting}",
                        f"**{desc}**\n\n"
                        f"**Current Status:** {status}\n\n"
                        f"{Theme.THIN_SEP}\n"
                        f"Use `/config {setting} value:enable` or `/config {setting} value:disable` to toggle.",
                        Theme.INFO
                    ),
                    ephemeral=True
                )
                return

            lowered = value.strip().lower()
            if lowered in ("enable", "on", "true", "1", "yes"):
                set_config(setting, True)
                await interaction.response.send_message(
                    embed=success_embed("✅ Enabled", f"**{desc}** is now 🟢 **Enabled**"),
                    ephemeral=True
                )
            elif lowered in ("disable", "off", "false", "0", "no"):
                set_config(setting, False)
                await interaction.response.send_message(
                    embed=success_embed("✅ Disabled", f"**{desc}** is now 🔴 **Disabled**"),
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    embed=error_embed("❌ Invalid Value", f"Use `enable` or `disable` for toggle settings.\nExample: `/config {setting} value:enable`"),
                    ephemeral=True
                )
            return

        # ── String settings ──
        if setting in self.STRING_SETTINGS:
            if not value:
                current = get_config(setting, "Not set")
                await interaction.response.send_message(
                    embed=make_embed("📋 Current Config", f"**{setting}:** `{current}`", Theme.INFO),
                    ephemeral=True
                )
                return

            set_config(setting, value)
            await interaction.response.send_message(
                embed=success_embed("✅ Config Updated", f"**{setting}** set to `{value}`"),
                ephemeral=True
            )
            return

        # ── Numeric settings ──
        if not value:
            current = get_config(setting, "Not set")
            await interaction.response.send_message(
                embed=make_embed("📋 Current Config", f"**{setting}:** `{current}`", Theme.INFO),
                ephemeral=True
            )
            return

        try:
            int_value = int(value)
            if setting == "registration_open_hour" and (int_value < 0 or int_value > 23):
                await interaction.response.send_message(
                    embed=error_embed("❌ Invalid Value", "registration_open_hour must be between 0 and 23."),
                    ephemeral=True
                )
                return
            if setting == "registration_open_minute" and (int_value < 0 or int_value > 59):
                await interaction.response.send_message(
                    embed=error_embed("❌ Invalid Value", "registration_open_minute must be between 0 and 59."),
                    ephemeral=True
                )
                return
            if setting == "default_reserved_slots" and (int_value < 0 or int_value > 3):
                await interaction.response.send_message(
                    embed=error_embed("❌ Invalid Value", "default_reserved_slots must be between 0 and 3."),
                    ephemeral=True
                )
                return

            set_config(setting, int_value)
            await interaction.response.send_message(
                embed=success_embed("✅ Config Updated", f"**{setting}** set to `{int_value}`"),
                ephemeral=True
            )
        except ValueError:
            set_config(setting, value)
            await interaction.response.send_message(
                embed=success_embed("✅ Config Updated", f"**{setting}** set to `{value}`"),
                ephemeral=True
            )

    # ─────────────── VIEW CONFIG ───────────────

    @staticmethod
    def paginate_logical_page(title: str, description_lines: list, fields: list, color: discord.Color) -> list:
        """
        Split a logical configuration page into one or more embeds to guarantee
        they never exceed Discord's limits.
        """
        embeds = []
        
        # 1. Paginate by description if description is very long
        desc_chunks = []
        current_desc = []
        current_len = 0
        for line in description_lines:
            line_len = len(line) + 1
            if current_len + line_len > 3800:
                desc_chunks.append("\n".join(current_desc))
                current_desc = [line]
                current_len = line_len
            else:
                current_desc.append(line)
                current_len += line_len
        if current_desc:
            desc_chunks.append("\n".join(current_desc))
            
        if not desc_chunks:
            desc_chunks = [""]

        # 2. Paginate fields
        field_chunks = []
        current_fields = []
        field_char_count = 0
        for f_name, f_val, f_inline in fields:
            f_len = len(f_name) + len(f_val)
            if len(current_fields) >= 20 or field_char_count + f_len > 4000:
                field_chunks.append(current_fields)
                current_fields = [(f_name, f_val, f_inline)]
                field_char_count = f_len
            else:
                current_fields.append((f_name, f_val, f_inline))
                field_char_count += f_len
        if current_fields:
            field_chunks.append(current_fields)
            
        if not field_chunks:
            field_chunks = [[]]

        max_subpages = max(len(desc_chunks), len(field_chunks))
        for i in range(max_subpages):
            d_text = desc_chunks[i] if i < len(desc_chunks) else ""
            f_list = field_chunks[i] if i < len(field_chunks) else []
            
            subpage_title = title
            if max_subpages > 1:
                subpage_title = f"{title} (Part {i+1}/{max_subpages})"
                
            emb = make_embed(title=subpage_title, desc=d_text, color=color)
            for f_name, f_val, f_inline in f_list:
                if len(f_val) > 1024:
                    val_chunks = [f_val[k:k+1000] for k in range(0, len(f_val), 1000)]
                    for idx, vc in enumerate(val_chunks):
                        emb.add_field(name=f"{f_name} (Part {idx+1})" if len(val_chunks) > 1 else f_name, value=vc, inline=f_inline)
                else:
                    emb.add_field(name=f_name, value=f_val, inline=f_inline)
            embeds.append(emb)
            
        return embeds

    @app_commands.command(name="viewconfig", description="[Admin] View all bot configuration")
    @app_commands.checks.has_permissions(administrator=True)
    async def view_config(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # Retrieve MongoDB client and config collection
        from database import bot_config as config_collection, _client
        all_configs = list(config_collection.find({}))
        db_configs = {cfg.get("key"): cfg.get("value") for cfg in all_configs}

        # Load values directly from config files, db, or runtime settings
        import config
        from config import (
            DEFAULT_GROUP_CAPACITY, DEFAULT_GROUP_COUNT, DEFAULT_RESERVED_SLOTS,
            DEFAULT_CATEGORY_NAME, TIMEZONE_OFFSET, REGISTRATION_OPEN_HOUR, REGISTRATION_OPEN_MINUTE,
            DEFAULT_LOCK_MINUTES, DEFAULT_REMINDER_LEAD_MINUTES, load_schedule, get_today_event_id
        )
        from cogs.registration import is_registration_open
        from models import group as group_model, registration as reg_model
        from models.scrim import get_all_scrims
        import inspect

        guild = interaction.guild
        bot = interaction.client

        # Get active scrims
        all_scrims = get_all_scrims()
        active_scrims = [s for s in all_scrims if s.get("status") == "active"]

        # Date for today's events
        import datetime
        utc_now = datetime.datetime.utcnow()
        local_now = utc_now + datetime.timedelta(hours=TIMEZONE_OFFSET)
        date_str = local_now.strftime("%Y-%m-%d")

        # 1. 🏆 Page 1 - General Info & Stats
        try:
            _client.admin.command("ping")
            db_status = "🟢 Connected"
        except Exception:
            db_status = "🔴 Disconnected / Error"

        from config import MONGO_URI
        masked_mongo_uri = "`Hidden / Masked`"
        if MONGO_URI:
            import re
            masked_mongo_uri = re.sub(r'mongodb(\+srv)?://([^:]+):([^@]+)@', r'mongodb\1://***:***@', MONGO_URI)

        bot_status = "🟢 Online"
        bot_latency = f"{bot.latency * 1000:.0f} ms"
        bot_version = db_configs.get("version", "2.0.0 (Esports Edition)")
        bot_prefix = db_configs.get("prefix", "!")
        total_slash_commands = len(bot.tree.get_commands())
        reminders_cog = bot.get_cog("RemindersCog")
        scheduler_status = "Active" if reminders_cog and reminders_cog.reminder_loop.is_running() else "Inactive"

        general_lines = [
            f"◆ **Bot Status:** {bot_status} (Latency: `{bot_latency}`)",
            f"◆ **Database Status:** {db_status}",
            f"◆ **MongoDB Connection:** `{masked_mongo_uri}`",
            f"◆ **Version:** `{bot_version}`",
            f"◆ **Prefix:** `{bot_prefix}`",
            f"◆ **Slash Commands:** `{total_slash_commands}` synced",
            f"◆ **Scheduler Status:** `{scheduler_status}`",
            f"◆ **Total Active Scrims:** `{len(active_scrims)}`",
            "",
            "**── Active Scrims List ──**"
        ]
        for s in active_scrims:
            general_lines.append(f"  ◆ **{s.get('scrim_id')}** ─ {s.get('name')} (Owner: <@{s.get('owner_id')}>)")
        if not active_scrims:
            general_lines.append("  *No active scrims configured.*")

        # 2. 👥 Page 2 - Registration
        is_open, open_h, open_m, _ = await is_registration_open()
        reg_status = "🟢 Open / Accepting Registrations" if is_open else "🔴 Closed / Locked"
        
        ampm = "PM" if open_h >= 12 else "AM"
        display_h = open_h if 0 < open_h <= 12 else (open_h - 12 if open_h > 12 else 12)
        open_time_str = f"{display_h:02d}:{open_m:02d} {ampm} IST"
        
        lock_minutes = db_configs.get("lock_minutes", DEFAULT_LOCK_MINUTES)
        close_time_desc = f"Auto-Locks {lock_minutes} minutes before each group match start"
        
        toggle_auto_reg = db_configs.get("auto_registration_open", True)
        toggle_memory = db_configs.get("team_memory", True)
        toggle_waiting = db_configs.get("waiting_list", False)
        
        default_capacity = db_configs.get("default_group_capacity", DEFAULT_GROUP_CAPACITY)
        default_reserved = db_configs.get("default_reserved_slots", DEFAULT_RESERVED_SLOTS)

        def toggle_icon(val):
            return "🟢 Enabled" if val else "🔴 Disabled"

        registration_lines = [
            f"◆ **Registration Status:** {reg_status}",
            f"◆ **Auto-Registration Open:** {toggle_icon(toggle_auto_reg)}",
            f"◆ **Registration Open Time:** `{open_time_str}`",
            f"◆ **Registration Close/Lock:** `{close_time_desc}`",
            f"◆ **Default Capacity Per Group:** `{default_capacity}`",
            f"◆ **Default Reserved Slots:** `{default_reserved}`",
            f"◆ **Team Memory (30-day):** {toggle_icon(toggle_memory)}",
            f"◆ **Waiting List:** {toggle_icon(toggle_waiting)}",
        ]

        # 3. 📅 Page 3 - Schedule
        schedule_lines = []
        for s in active_scrims:
            s_id = s.get("scrim_id", "").upper()
            s_name = s.get("name", "")
            s_sched = s.get("schedule", [])
            if s_sched:
                schedule_lines.append(f"**✦ Scrim: {s_name} ({s_id}) Schedule:**")
                for item in s_sched:
                    g_num = item.get("group_number", 0) or item.get("group_id", "?")
                    m1 = item.get("match1", {})
                    m2 = item.get("match2", {})
                    m1_desc = f"{m1.get('map', 'TBD')} @ {m1.get('start', 'TBD')}"
                    m2_desc = f"{m2.get('map', 'TBD')} @ {m2.get('start', 'TBD')}"
                    schedule_lines.append(f"  ◆ Group **{g_num}**: Match 1: `{m1_desc}` │ Match 2: `{m2_desc}`")
            else:
                schedule_lines.append(f"**✦ Scrim: {s_name} ({s_id})**: *No custom schedule.*")
            schedule_lines.append("")
        
        if not any(s.get("schedule") for s in active_scrims):
            schedule = load_schedule()
            schedule_lines = ["**✦ Default Schedule:**"]
            for item in schedule:
                g_num = item.get("group_number", 0) or item.get("group_id", "?")
                m1 = item.get("match1", {})
                m2 = item.get("match2", {})
                m1_desc = f"{m1.get('map', 'TBD')} @ {m1.get('start', 'TBD')}"
                m2_desc = f"{m2.get('map', 'TBD')} @ {m2.get('start', 'TBD')}"
                schedule_lines.append(f"  ◆ Group **{g_num}**: Match 1: `{m1_desc}` │ Match 2: `{m2_desc}`")

        # 4. 👥 Page 4 - Teams & Slots
        teams_slots_lines = []
        for s in active_scrims:
            s_id = s.get("scrim_id", "").upper()
            s_name = s.get("name", "")
            
            scrim_event_id = get_today_event_id(s_id)
            scrim_groups = await asyncio.to_thread(group_model.get_all_groups, scrim_event_id, scrim_id=s_id)
            total_regs = await asyncio.to_thread(reg_model.count_registrations, scrim_event_id)
            
            s_cap = s.get("settings", {}).get("capacity", default_capacity)
            total_capacity = sum(g.get("capacity", s_cap) for g in scrim_groups)
            
            if len(scrim_groups) == 0:
                fallback_groups_count = s.get("settings", {}).get("group_count") or db_configs.get("default_group_count", DEFAULT_GROUP_COUNT)
                total_capacity = fallback_groups_count * s_cap
                remaining_slots = total_capacity
            else:
                remaining_slots = max(0, total_capacity - total_regs)
                
            teams_slots_lines.append(f"**✦ Scrim: {s_name} ({s_id})**")
            teams_slots_lines.append(f"  ◆ **Total Registered Teams:** `{total_regs}`")
            teams_slots_lines.append(f"  ◆ **Total Capacity:** `{total_capacity}`")
            teams_slots_lines.append(f"  ◆ **Remaining Slots:** `{remaining_slots}`")
            if total_capacity > 0:
                teams_slots_lines.append(f"  ◆ **Roster Fill:** {Theme.bar(total_regs, total_capacity, 14)}")
            
            group_lines = []
            for g in scrim_groups:
                gid = g.get("group_id")
                count = g.get("current_count", 0)
                cap = g.get("capacity", s_cap)
                reserved = g.get("reserved_slots", 0)
                pub_count = max(0, count - reserved)
                pub_cap = cap - reserved
                group_lines.append(f"    ▪ **Group {gid}:** `{pub_count}/{pub_cap}` registered")
            
            if group_lines:
                teams_slots_lines.extend(group_lines)
            else:
                teams_slots_lines.append("    *No active groups provisioned today.*")
            teams_slots_lines.append("")

        # 5. ⚙️ Page 5 - Modules
        modules_lines = []
        for s in active_scrims:
            s_id = s.get("scrim_id", "").upper()
            s_name = s.get("name", "")
            s_modules = s.get("modules", {})
            
            modules_lines.append(f"**✦ Scrim: {s_name} ({s_id}) Modules:**")
            if s_modules:
                for mod_name, mod_enabled in sorted(s_modules.items()):
                    icon = "🟢 Enabled" if mod_enabled else "🔴 Disabled"
                    modules_lines.append(f"  ◆ **{mod_name}:** {icon}")
            else:
                modules_lines.append("  *No custom modules configured.*")
            modules_lines.append("")

        # 6. 📢 Page 6 - Channels & Roles
        channels_lines = [
            "**── Global Config Channels ──**"
        ]
        reg_channel_id = get_channel_config("register")
        announcement_channel_id = db_configs.get("channel_announcement") or db_configs.get("channel_announcements")
        if not announcement_channel_id:
            ann_ch = discord.utils.get(guild.text_channels, name="announcements") or discord.utils.get(guild.text_channels, name="announcement")
            if ann_ch:
                announcement_channel_id = ann_ch.id
                
        result_channel_id = get_channel_config("leaderboard")
        admin_channel_id = get_channel_config("admin")
        admin_log_channel_id = get_channel_config("admin_log")
        registered_teams_channel_id = get_channel_config("registered_teams")

        channels_lines.append(f"  ◆ **Registration:** <#{reg_channel_id}> (`{reg_channel_id}`)" if reg_channel_id else "  ◆ **Registration:** `Not set`")
        channels_lines.append(f"  ◆ **Announcements:** <#{announcement_channel_id}> (`{announcement_channel_id}`)" if announcement_channel_id else "  ◆ **Announcements:** `Not set`")
        channels_lines.append(f"  ◆ **Leaderboard:** <#{result_channel_id}> (`{result_channel_id}`)" if result_channel_id else "  ◆ **Leaderboard:** `Not set`")
        channels_lines.append(f"  ◆ **Admin Command:** <#{admin_channel_id}> (`{admin_channel_id}`)" if admin_channel_id else "  ◆ **Admin Command:** `Not set`")
        channels_lines.append(f"  ◆ **Admin Log:** <#{admin_log_channel_id}> (`{admin_log_channel_id}`)" if admin_log_channel_id else "  ◆ **Admin Log:** `Not set`")
        channels_lines.append(f"  ◆ **Registration Receipts:** <#{registered_teams_channel_id}> (`{registered_teams_channel_id}`)" if registered_teams_channel_id else "  ◆ **Registration Receipts:** `Not set`")
        channels_lines.append("")
        
        for s in active_scrims:
            s_id = s.get("scrim_id", "").upper()
            s_name = s.get("name", "")
            s_channels = s.get("channels", {})
            channels_lines.append(f"**── Scrim: {s_name} ({s_id}) Channels ──**")
            if s_channels:
                for ch_type, ch_id in sorted(s_channels.items()):
                    ch_display = f"<#{ch_id}> (`{ch_id}`)" if ch_id else "`Not set`"
                    channels_lines.append(f"  ◆ **{ch_type}:** {ch_display}")
            else:
                channels_lines.append("  *No custom channels configured.*")
            channels_lines.append("")

        # Roles and parent categories
        category_set = set()
        for k, v in db_configs.items():
            if k.startswith("category_") and isinstance(v, int):
                category_set.add(v)
        role_mentions = []
        role_set = set()
        
        for s in active_scrims:
            s_id = s.get("scrim_id", "").upper()
            scrim_event_id = get_today_event_id(s_id)
            scrim_groups = await asyncio.to_thread(group_model.get_all_groups, scrim_event_id, scrim_id=s_id)
            for g in scrim_groups:
                cat_id = g.get("category_id")
                if cat_id:
                    category_set.add(cat_id)
                rid = g.get("role_id")
                gid = g.get("group_id")
                if rid:
                    role_mentions.append(f"  ◆ Group **{gid}** ({s_id}): <@&{rid}> (`{rid}`)")
                    role_set.add(rid)

        for k, v in db_configs.items():
            if (k.endswith("_role") or k.startswith("role_")) and isinstance(v, int) and v not in role_set:
                role_mentions.append(f"  ◆ **{k}:** <@&{v}> (`{v}`)")
                role_set.add(v)

        channels_lines.append("**── Parent Categories & Roles ──**")
        category_ids_str = ", ".join(f"<#{cid}> (`{cid}`)" for cid in sorted(category_set)) if category_set else "None"
        channels_lines.append(f"  ◆ **Parent Categories:** {category_ids_str}")
        channels_lines.append("")
        channels_lines.append("**── Discord Roles ──**")
        if role_mentions:
            channels_lines.extend(role_mentions)
        else:
            channels_lines.append("  *No active roles found.*")

        # 7. 🏆 Page 7 - Points & Results
        from config import DEFAULT_POSITION_POINTS
        points_lines = []
        for s in active_scrims:
            s_id = s.get("scrim_id", "").upper()
            s_name = s.get("name", "")
            s_points = s.get("points_config", {})
            s_settings = s.get("settings", {})
            
            m_format = s_settings.get("match_format", db_configs.get("match_format", "Squad TPP"))
            m_count = s_settings.get("match_count", db_configs.get("match_count", 2))
            
            points_lines.append(f"**✦ Scrim: {s_name} ({s_id})**")
            points_lines.append(f"  ◆ **Match Format:** `{m_format}`")
            points_lines.append(f"  ◆ **Match Count:** `{m_count}`")
            points_lines.append(f"  ◆ **Kill Points:** `{s_points.get('kill_points', 1)}`")
            
            pos_pts = s_points.get("position_points", {})
            if pos_pts:
                pts_summary = ", ".join(f"#{rank}: {pts}pt" for rank, pts in sorted(pos_pts.items(), key=lambda x: int(x[0])) if pts > 0)
                points_lines.append(f"  ◆ **Placement Points:** `{pts_summary or 'No points configured'}`")
            else:
                points_lines.append("  ◆ **Placement Points:** `None`")
            points_lines.append("")
            
        if not active_scrims:
            kill_points = db_configs.get("kill_points", 1)
            pos_pts = db_configs.get("position_points", DEFAULT_POSITION_POINTS)
            points_lines.append("**✦ Global Fallback Config:**")
            points_lines.append(f"  ◆ **Kill Points:** `{kill_points}`")
            if isinstance(pos_pts, dict):
                pts_summary = ", ".join(f"#{rank}: {pts}pt" for rank, pts in sorted(pos_pts.items(), key=lambda x: int(x[0])) if pts > 0)
                points_lines.append(f"  ◆ **Placement Points:** `{pts_summary}`")
            else:
                points_lines.append(f"  ◆ **Placement Points:** `{pos_pts}`")

        # 8. ⚙️ Page 8 - Advanced Settings
        displayed_keys = {
            "version", "prefix", "token", "mongo_uri", "commands_hash", "commands_synced", "sync_commands_on_startup",
            "registration_open_hour", "registration_open_minute", "lock_minutes", "default_group_capacity", 
            "default_reserved_slots", "waiting_list_status", "team_memory", "waiting_list", "auto_registration_open", 
            "last_open_date", "schedule", "kill_points", "position_points", "match_format", "match_count",
            "DEFAULT_POSITION_POINTS", "RANK_EMOJIS", "auto_group_generation", "midnight_reset", "match_reminders", 
            "last_reset_date", "default_group_count", "default_category_name", "event_name", "event_mode", 
            "group_naming_pattern", "default_reserved_slots", "registration_open_subscribers", "multi_scrim_migrated"
        }
        for k in db_configs.keys():
            if k.startswith("category_") or k.startswith("channel_") or k.startswith("role_") or k.endswith("_role") or k.endswith("_channel"):
                displayed_keys.add(k)

        advanced_settings = []
        for k, v in db_configs.items():
            if k not in displayed_keys:
                advanced_settings.append(f"  ◆ **{k}:** `{v}`")
        for k, v in config_configs.items():
            if k not in displayed_keys and k.lower() not in displayed_keys and k.lower() not in db_configs and k not in db_configs:
                advanced_settings.append(f"  ◆ **{k}:** `{v}`")

        advanced_lines = ["**── Additional Database & Config Constants ──**"]
        for line in sorted(advanced_settings):
            advanced_lines.append(line)
        if len(advanced_lines) == 1:
            advanced_lines.append("  *No custom advanced settings registered.*")

        # Compile embeds with logic pagination split support
        all_embeds = []
        all_embeds.extend(self.paginate_logical_page("⚙️ Config: Page 1 - General", general_lines, [], Theme.TEAL))
        all_embeds.extend(self.paginate_logical_page("⚙️ Config: Page 2 - Registration", registration_lines, [], Theme.INFO))
        all_embeds.extend(self.paginate_logical_page("⚙️ Config: Page 3 - Schedule", schedule_lines, [], Theme.WARNING))
        all_embeds.extend(self.paginate_logical_page("⚙️ Config: Page 4 - Teams & Slots", teams_slots_lines, [], Theme.ACCENT))
        all_embeds.extend(self.paginate_logical_page("⚙️ Config: Page 5 - Modules", modules_lines, [], Theme.PREMIUM))
        all_embeds.extend(self.paginate_logical_page("⚙️ Config: Page 6 - Channels & Roles", channels_lines, [], Theme.ROSE))
        all_embeds.extend(self.paginate_logical_page("⚙️ Config: Page 7 - Points & Results", points_lines, [], Theme.GOLD))
        all_embeds.extend(self.paginate_logical_page("⚙️ Config: Page 8 - Advanced Settings", advanced_lines, [], Theme.ORANGE))

        # Apply correct index footer to all embeds
        total_pages = len(all_embeds)
        for idx, emb in enumerate(all_embeds):
            emb.set_footer(text=f"Page {idx+1} of {total_pages} │ Mack Bot Configuration Overview")

        # Send pagination view
        if all_embeds:
            view = ConfigPaginationView(all_embeds, interaction.user.id)
            await interaction.followup.send(embed=all_embeds[0], view=view, ephemeral=True)
        else:
            await interaction.followup.send("⚠️ No configuration pages found.", ephemeral=True)

    # ─────────────── UNBAN COMMAND ───────────────

    @app_commands.command(name="unban", description="[Admin] Unban a player")
    @app_commands.describe(member="The member to unban")
    @app_commands.checks.has_permissions(administrator=True)
    async def unban_cmd(self, interaction: discord.Interaction, member: discord.Member):
        owner_id = str(member.id)
        success = punishment.unban_user(owner_id)

        if not success:
            await interaction.response.send_message(
                embed=error_embed("⚠️ Not Banned", f"{member.mention} is not currently banned."),
                ephemeral=True
            )
            return

        embed = make_embed(
            "🔓 Player Unbanned",
            f"{Theme.SEP}\n\n"
            f"👤 **Player:** {member.mention}\n"
            f"**Unbanned by:** {interaction.user.mention}\n\n{Theme.SEP}",
            Theme.SUCCESS
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

        log_ch_id = get_channel_config("admin_log")
        if log_ch_id:
            log_ch = interaction.guild.get_channel(log_ch_id)
            if log_ch:
                await log_ch.send(embed=embed)

        try:
            await member.send(embed=make_embed(
                "🔓 You've Been Unbanned",
                f"You can now register for scrims again.",
                Theme.SUCCESS
            ))
        except: pass

    # ─────────────── BAN LIST ───────────────

    @app_commands.command(name="banlist", description="[Admin] View all active bans")
    @app_commands.checks.has_permissions(administrator=True)
    async def banlist_cmd(self, interaction: discord.Interaction):
        bans = punishment.get_active_bans()

        if not bans:
            await interaction.response.send_message(
                embed=success_embed("✅ No Bans", "No players are currently banned."),
                ephemeral=True
            )
            return

        lines = []
        for ban in bans:
            uid = ban.get("owner_id", "?")
            reason = ban.get("reason", "No reason")
            exp = ban.get("expires_at", "?")
            if exp == "never":
                exp_display = "Permanent"
            else:
                try:
                    exp_dt = datetime.datetime.fromisoformat(exp) + datetime.timedelta(hours=TIMEZONE_OFFSET)
                    exp_display = exp_dt.strftime("%Y-%m-%d %I:%M %p")
                except:
                    exp_display = exp
            lines.append(f"• <@{uid}> — `{reason}`\n  └ Expires: `{exp_display}`")

        embed = make_embed(
            "🔨 Active Bans",
            f"{Theme.SEP}\n\n" + "\n".join(lines) + f"\n\n{Theme.SEP}",
            Theme.ERROR
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ─────────────── /reserve COMMAND GROUP ───────────────

    reserve_group = app_commands.Group(name="reserve", description="[Admin] Manage reserved slots")

    @reserve_group.command(name="slots", description="Set the number of reserved slots per group (0-3)")
    @app_commands.describe(count="Number of reserved slots (0 to 3)")
    @app_commands.checks.has_permissions(administrator=True)
    async def reserve_slots(self, interaction: discord.Interaction, count: int):
        if count < 0 or count > 3:
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid Count", "Reserved slots must be between 0 and 3."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Save as default for future provisions
        await asyncio.to_thread(set_config, "default_reserved_slots", count)

        # Update all groups for today's event
        event_id = get_today_event_id()
        updated_count = await asyncio.to_thread(group_model.set_all_reserved_slots, event_id, count)

        # Refresh all rosters and registers
        guild = interaction.guild
        all_groups = await asyncio.to_thread(group_model.get_all_groups, event_id)
        for g in all_groups:
            await update_group_roster(guild, event_id, g["group_id"])

        # Update slot availability board
        await update_registration_board(guild, event_id)

        await interaction.followup.send(
            embed=success_embed(
                "✅ Reserved Slots Updated",
                f"{Theme.SEP}\n\n"
                f"Default reserved slots count set to: **{count}**\n"
                f"Updated **{updated_count}** active groups for today.\n\n{Theme.SEP}"
            ),
            ephemeral=True
        )

    @reserve_group.command(name="fill", description="Fill a reserved slot in a group with a team name")
    @app_commands.describe(
        group_id="Group ID (e.g. G0001)",
        slot="Roster slot number (1 to reserved count)",
        team_name="Name of the team to assign"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def reserve_fill(self, interaction: discord.Interaction, group_id: str, slot: int, team_name: str):
        event_id = get_today_event_id()
        group_doc = await asyncio.to_thread(group_model.get_group, event_id, group_id)
        if not group_doc:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Group `{group_id}` not found for today."),
                ephemeral=True
            )
            return

        reserved = group_doc.get("reserved_slots", 0)
        if slot < 1 or slot > reserved:
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid Slot", f"Group `{group_id}` only has `{reserved}` reserved slots (1-{reserved})."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        updated_group = await asyncio.to_thread(group_model.fill_reserved_slot, event_id, group_id, slot, team_name)
        if not updated_group:
            await interaction.followup.send(
                embed=error_embed("❌ Error", "Could not fill reserved slot."),
                ephemeral=True
            )
            return

        # Refresh roster in the group channel and board
        await update_group_roster(interaction.guild, event_id, group_id)
        await update_registration_board(interaction.guild, event_id)

        await interaction.followup.send(
            embed=success_embed(
                "✅ Slot Filled",
                f"{Theme.SEP}\n\n"
                f"Filled Slot **{slot:02d}** in **{group_id}** with team **{team_name}**.\n\n{Theme.SEP}"
            ),
            ephemeral=True
        )

    @reserve_group.command(name="clear", description="Clear a filled reserved slot back to empty RESERVED status")
    @app_commands.describe(
        group_id="Group ID (e.g. G0001)",
        slot="Roster slot number to clear"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def reserve_clear(self, interaction: discord.Interaction, group_id: str, slot: int):
        event_id = get_today_event_id()
        group_doc = await asyncio.to_thread(group_model.get_group, event_id, group_id)
        if not group_doc:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Group `{group_id}` not found for today."),
                ephemeral=True
            )
            return

        reserved = group_doc.get("reserved_slots", 0)
        if slot < 1 or slot > reserved:
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid Slot", f"Group `{group_id}` only has `{reserved}` reserved slots (1-{reserved})."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        updated_group = await asyncio.to_thread(group_model.clear_reserved_slot, event_id, group_id, slot)
        if not updated_group:
            await interaction.followup.send(
                embed=error_embed("❌ Error", "Could not clear reserved slot."),
                ephemeral=True
            )
            return

        # Refresh roster in the group channel and board
        await update_group_roster(interaction.guild, event_id, group_id)
        await update_registration_board(interaction.guild, event_id)

        await interaction.followup.send(
            embed=success_embed(
                "✅ Slot Cleared",
                f"{Theme.SEP}\n\n"
                f"Cleared Slot **{slot:02d}** in **{group_id}** back to reserved status.\n\n{Theme.SEP}"
            ),
            ephemeral=True
        )

    @reserve_group.command(name="view", description="View reserved slots configuration and status")
    @app_commands.checks.has_permissions(administrator=True)
    async def reserve_view(self, interaction: discord.Interaction):
        event_id = get_today_event_id()
        all_groups = await asyncio.to_thread(group_model.get_all_groups, event_id)
        if not all_groups:
            await interaction.response.send_message(
                embed=error_embed("❌ No Groups", "No groups found for today."),
                ephemeral=True
            )
            return

        lines = []
        for g in all_groups:
            gid = g["group_id"]
            res_count = g.get("reserved_slots", 0)
            res_teams = g.get("reserved_teams", {})
            filled_lines = []
            for s in range(1, res_count + 1):
                tname = res_teams.get(str(s), "🔴 *Empty*")
                filled_lines.append(f"  └ Slot {s:02d}: **{tname}**")
            
            lines.append(f"**✦ Group {gid}** ({res_count} reserved slots)")
            if filled_lines:
                lines.extend(filled_lines)
            else:
                lines.append("  └ *None*")

        embed = make_embed(
            "📋 Reserved Slots Status",
            f"{Theme.SEP}\n\n" + "\n".join(lines) + f"\n\n{Theme.SEP}",
            Theme.INFO
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class ConfigPaginationView(ui.View):
    """View class for paginating the /viewconfig command output."""

    def __init__(self, pages: list, user_id: int):
        super().__init__(timeout=300)
        self.pages = pages
        self.user_id = user_id
        self.current_page = 0
        self.update_buttons()

    def update_buttons(self):
        self.prev_btn.disabled = self.current_page == 0
        self.next_btn.disabled = self.current_page == len(self.pages) - 1

    @ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary, custom_id="config_prev")
    async def prev_btn(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ This is not your config menu.", ephemeral=True)
            return
        if self.current_page > 0:
            self.current_page -= 1
            self.update_buttons()
            embed = self.pages[self.current_page]
            await interaction.response.edit_message(embed=embed, view=self)

    @ui.button(label="Next ▶", style=discord.ButtonStyle.primary, custom_id="config_next")
    async def next_btn(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ This is not your config menu.", ephemeral=True)
            return
        if self.current_page < len(self.pages) - 1:
            self.current_page += 1
            self.update_buttons()
            embed = self.pages[self.current_page]
            await interaction.response.edit_message(embed=embed, view=self)


# ═══════════════════ ADMIN PANEL QUICK VIEW ═══════════════════

class AdminPanelQuickView(ui.View):
    """Quick admin panel from /panel command (non-persistent)."""

    def __init__(self):
        super().__init__(timeout=300)

    @ui.button(label="🔔 Match Reminder", style=discord.ButtonStyle.primary, row=0)
    async def reminder_btn(self, interaction: discord.Interaction, button: ui.Button):
        event_id = get_today_event_id()
        all_groups = group_model.get_all_groups(event_id)

        if not all_groups:
            await interaction.response.send_message(
                embed=error_embed("❌ No Groups", "No groups provisioned for today."),
                ephemeral=True
            )
            return

        options = []
        for g in all_groups[:25]:
            gid = g["group_id"]
            sent = "✅ Sent" if g.get("reminder_sent") else "⏳ Pending"
            options.append(discord.SelectOption(
                label=f"Group {gid} — {sent}",
                value=gid, emoji="🔔"
            ))

        view = ui.View(timeout=60)
        select = ReminderGroupSelect(event_id, options)
        view.add_item(select)

        await interaction.response.send_message(
            embed=make_embed("🔔 Select Group", "Choose which group to send a reminder to.", Theme.ACCENT),
            view=view, ephemeral=True
        )

    @ui.button(label="📋 Publish Slot List", style=discord.ButtonStyle.secondary, row=0)
    async def slot_list_btn(self, interaction: discord.Interaction, button: ui.Button):
        event_id = get_today_event_id()
        all_groups = group_model.get_all_groups(event_id)

        if not all_groups:
            await interaction.response.send_message(
                embed=error_embed("❌ No Groups", "No groups for today."),
                ephemeral=True
            )
            return

        options = [discord.SelectOption(label=f"Group {g['group_id']}", value=g["group_id"], emoji="📋")
                   for g in all_groups[:25]]

        view = ui.View(timeout=60)
        select = SlotListGroupSelect(event_id, options)
        view.add_item(select)

        await interaction.response.send_message(
            embed=make_embed("📋 Select Group", "Choose which group's slot list to publish.", Theme.ACCENT),
            view=view, ephemeral=True
        )

    @ui.button(label="🔧 Manage Matches", style=discord.ButtonStyle.secondary, row=1)
    async def manage_btn(self, interaction: discord.Interaction, button: ui.Button):
        view = AdminManageSubView(get_today_event_id(), None)
        await interaction.response.send_message(
            embed=make_embed(
                "🔧 Manage Matches",
                f"{Theme.SEP}\n\n"
                f"**✏️ Edit Match** — Change IDP, start time, or map for a group\n"
                f"**🔀 Move Team** — Admin override to move a team between groups\n\n{Theme.SEP}",
                Theme.ACCENT
            ),
            view=view, ephemeral=True
        )

    @ui.button(label="🔨 Punish Team", style=discord.ButtonStyle.danger, row=1)
    async def punish_btn(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(PunishModal())

    @ui.button(label="🏆 Qualified Teams", style=discord.ButtonStyle.success, row=1)
    async def qualified_btn(self, interaction: discord.Interaction, button: ui.Button):
        event_id = get_today_event_id()
        from database import match_results as results_collection
        results = list(results_collection.find({"event_id": event_id}))

        if not results:
            await interaction.response.send_message(
                embed=error_embed("❌ No Standings", "No match results recorded yet today."),
                ephemeral=True
            )
            return

        team_totals = {}
        for r in results:
            tk = r.get("team_key") or r.get("team_name", "").strip().lower()
            if not tk:
                continue
            if tk not in team_totals:
                team_totals[tk] = {"team_name": r.get("team_name", "?"), "total_kills": 0, "total_points": 0}
            team_totals[tk]["total_kills"] += r.get("kills", 0)
            team_totals[tk]["total_points"] += r.get("total_points", 0)

        sorted_teams = sorted(team_totals.values(), key=lambda x: (x["total_points"], x["total_kills"]), reverse=True)
        lines = [f"{get_rank_emoji(i+1)} **{t['team_name']}** ─ `{t['total_points']}` pts │ 💀 `{t['total_kills']}`"
                 for i, t in enumerate(sorted_teams[:16])]

        await interaction.response.send_message(
            embed=make_embed("🏆 Top Qualifying Teams",
                             f"{Theme.SEP}\n\n" + "\n".join(lines) + f"\n\n{Theme.SEP}", Theme.GOLD),
            ephemeral=True
        )


class ReminderGroupSelect(ui.Select):
    def __init__(self, event_id, options):
        self.event_id = event_id
        super().__init__(placeholder="🔔 Select group…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        gid = self.values[0]
        cog = interaction.client.get_cog("RemindersCog")
        if cog:
            await cog.remind_group.callback(cog, interaction, group_id=gid)
        else:
            await interaction.response.send_message(
                embed=error_embed("❌ Error", "Reminders cog not loaded."),
                ephemeral=True
            )


class SlotListGroupSelect(ui.Select):
    def __init__(self, event_id, options):
        self.event_id = event_id
        super().__init__(placeholder="📋 Select group…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        gid = self.values[0]
        cog = interaction.client.get_cog("RemindersCog")
        if cog:
            await cog.publish_slot_list.callback(cog, interaction, group_id=gid)
        else:
            await interaction.response.send_message(
                embed=error_embed("❌ Error", "Reminders cog not loaded."),
                ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(AdminPanelCog(bot))
