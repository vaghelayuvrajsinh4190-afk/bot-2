"""
Mack Bot Tortuga — Admin Panel Cog
/panel command with buttons for Match Reminder, Publish Slot List,
Manage Matches, Punish Team, and Qualified Teams.
Phase 5 of the upgrade plan.
"""

import datetime
import discord
from discord.ext import commands
from discord import app_commands, ui

from config import Theme, TIMEZONE_OFFSET, DEFAULT_LOCK_MINUTES, get_rank_emoji
from utils.embeds import make_embed, error_embed, success_embed
from models import group as group_model, registration as reg_model, punishment
from database import get_config, set_config, get_channel_config, set_channel_config


# ═══════════════════ HELPERS ═══════════════════

def get_today_event_id():
    utc_now = datetime.datetime.utcnow()
    local_now = utc_now + datetime.timedelta(hours=TIMEZONE_OFFSET)
    return local_now.strftime("%Y-%m-%d")


# ═══════════════════ MODALS ═══════════════════

class EditMatchModal(ui.Modal, title="✏️ Edit Match Details"):
    """Modal for editing a group's match IDP, start time, and map."""
    
    group_id_input = ui.TextInput(label="Group ID", placeholder="G0001", max_length=10)
    match_num = ui.TextInput(label="Match Number (1 or 2)", placeholder="1", max_length=1)
    idp_time = ui.TextInput(label="IDP Time", placeholder="2:00 PM", required=False)
    start_time = ui.TextInput(label="Start Time", placeholder="2:10 PM", required=False)
    map_name = ui.TextInput(label="Map", placeholder="Erangel", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        event_id = get_today_event_id()
        gid = self.group_id_input.value.strip().upper()
        
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

        group_doc = group_model.get_group(event_id, gid)
        if not group_doc:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Group `{gid}` not found for today."),
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

        group_model.update_match_details(event_id, gid, match_n, details)
        
        updates = "\n".join([f"  ◆ **{k}:** `{v}`" for k, v in details.items()])
        await interaction.response.send_message(
            embed=success_embed(
                f"✅ Match {match_n} Updated — Group {gid}",
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
        from pymongo import ReturnDocument
        from database import groups as groups_collection
        
        # Force increment target
        groups_collection.update_one(
            {"event_id": event_id, "group_id": new_gid},
            {"$inc": {"current_count": 1}}
        )
        # Decrement source
        group_model.release_slot(event_id, old_gid)
        # Update registration
        reg_model.move_registration(owner_id, event_id, new_gid)

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
                    except: pass
                if new_role:
                    try: await member.add_roles(new_role)
                    except: pass

        team_name = reg.get("team_name", "???")
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
            # Remove roles
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


# ═══════════════════ PANEL VIEW ═══════════════════

class AdminPanelView(ui.View):
    """The admin panel with 5 action buttons."""
    
    def __init__(self):
        super().__init__(timeout=300)

    @ui.button(label="🔔 Match Reminder", style=discord.ButtonStyle.primary, row=0)
    async def reminder_btn(self, interaction: discord.Interaction, button: ui.Button):
        """Prompt admin for group ID and fire reminder."""
        event_id = get_today_event_id()
        all_groups = group_model.get_all_groups(event_id)
        
        if not all_groups:
            await interaction.response.send_message(
                embed=error_embed("❌ No Groups", "No groups provisioned for today."),
                ephemeral=True
            )
            return

        # Build dropdown of groups
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
        """Show sub-options: Edit Match Details or Move Team."""
        view = ManageSubView()
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


class ManageSubView(ui.View):
    """Sub-view with Edit Match and Move Team buttons."""
    def __init__(self):
        super().__init__(timeout=60)

    @ui.button(label="✏️ Edit Match Details", style=discord.ButtonStyle.primary)
    async def edit_match(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(EditMatchModal())

    @ui.button(label="🔀 Move Team", style=discord.ButtonStyle.secondary)
    async def move_team(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(MoveTeamModal())


class ReminderGroupSelect(ui.Select):
    def __init__(self, event_id, options):
        self.event_id = event_id
        super().__init__(placeholder="🔔 Select group…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        gid = self.values[0]
        # Fire reminder via the reminders cog
        cog = interaction.client.get_cog("RemindersCog")
        if cog:
            # Create a fake interaction-like call
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


# ═══════════════════ COG ═══════════════════

class AdminPanelCog(commands.Cog):
    """Admin panel with /panel and /config commands."""

    def __init__(self, bot):
        self.bot = bot

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
            f"{Theme.SEP}\n\n"
            f"╭── 📊 **Today's Overview** ──╮\n"
            f"│\n"
            f"│  📅 **Event:** `{event_id}`\n"
            f"│  👥 **Groups:** `{len(all_groups)}`\n"
            f"│  📋 **Registrations:** `{total_regs}/{total_capacity}`\n"
            f"│  🔒 **Locked:** `{locked_count}/{len(all_groups)}`\n"
            f"│  {Theme.bar(total_regs, total_capacity, 14)}\n"
            f"│\n"
            f"╰────────────────────────────╯\n\n"
            f"{Theme.THIN_SEP}\n\n"
            f"Use the buttons below to manage today's scrims:\n\n{Theme.SEP}",
            Theme.PREMIUM,
            f"Admin: {interaction.user.display_name}"
        )
        await interaction.response.send_message(embed=embed, view=AdminPanelView(), ephemeral=True)

    # ─────────────── CONFIG COMMANDS ───────────────

    @app_commands.command(name="config", description="[Admin] Configure bot channels and settings")
    @app_commands.describe(
        setting="What to configure",
        channel="Channel to set (for channel settings)",
        value="Value to set (for non-channel settings)"
    )
    @app_commands.choices(setting=[
        app_commands.Choice(name="register_channel", value="register"),
        app_commands.Choice(name="admin_channel", value="admin"),
        app_commands.Choice(name="admin_log_channel", value="admin_log"),
        app_commands.Choice(name="leaderboard_channel", value="leaderboard"),
        app_commands.Choice(name="default_group_count", value="default_group_count"),
        app_commands.Choice(name="default_group_capacity", value="default_group_capacity"),
        app_commands.Choice(name="reminder_lead_minutes", value="reminder_lead_minutes"),
        app_commands.Choice(name="lock_minutes", value="lock_minutes"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def config_cmd(
        self,
        interaction: discord.Interaction,
        setting: str,
        channel: discord.TextChannel = None,
        value: str = None
    ):
        # Channel settings
        if setting in ("register", "admin", "admin_log", "leaderboard"):
            if not channel:
                current = get_channel_config(setting)
                if current:
                    await interaction.response.send_message(
                        embed=make_embed(
                            "📋 Current Config",
                            f"**{setting}_channel:** <#{current}>",
                            Theme.INFO
                        ),
                        ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        embed=make_embed(
                            "📋 Not Set",
                            f"**{setting}_channel** is not configured.\n"
                            f"Use `/config {setting} #channel` to set it.",
                            Theme.WARNING
                        ),
                        ephemeral=True
                    )
                return

            set_channel_config(setting, channel.id)
            await interaction.response.send_message(
                embed=success_embed(
                    "✅ Config Updated",
                    f"**{setting}_channel** set to {channel.mention}"
                ),
                ephemeral=True
            )
        else:
            # Non-channel settings
            if not value:
                current = get_config(setting, "Not set")
                await interaction.response.send_message(
                    embed=make_embed("📋 Current Config", f"**{setting}:** `{current}`", Theme.INFO),
                    ephemeral=True
                )
                return

            try:
                int_value = int(value)
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

    @app_commands.command(name="viewconfig", description="[Admin] View all bot configuration")
    @app_commands.checks.has_permissions(administrator=True)
    async def view_config(self, interaction: discord.Interaction):
        from database import bot_config as config_collection
        all_configs = list(config_collection.find({}))
        
        if not all_configs:
            await interaction.response.send_message(
                embed=make_embed("📋 No Configuration", "No settings configured yet.\nUse `/config` to get started.", Theme.WARNING),
                ephemeral=True
            )
            return

        lines = []
        for cfg in all_configs:
            key = cfg.get("key", "?")
            val = cfg.get("value", "?")
            if isinstance(val, int) and val > 1000000000:  # Looks like a channel/role ID
                lines.append(f"  ◆ **{key}:** <#{val}> (`{val}`)")
            else:
                lines.append(f"  ◆ **{key}:** `{val}`")

        embed = make_embed(
            "📋 Bot Configuration",
            f"{Theme.SEP}\n\n" + "\n".join(lines) + f"\n\n{Theme.SEP}",
            Theme.PREMIUM
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

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

        # Log
        log_ch_id = get_channel_config("admin_log")
        if log_ch_id:
            log_ch = interaction.guild.get_channel(log_ch_id)
            if log_ch:
                await log_ch.send(embed=embed)

        # DM
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


async def setup(bot):
    await bot.add_cog(AdminPanelCog(bot))
