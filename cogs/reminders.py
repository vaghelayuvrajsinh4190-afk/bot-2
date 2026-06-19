"""
Mack Bot Tortuga — Reminders Cog
Handles match reminders, cancel/reschedule buttons, auto-lock, and slot list publishing.
Phase 4 of the upgrade plan.
"""

import datetime
import re
import discord
from discord.ext import commands, tasks
from discord import app_commands, ui

from config import Theme, TIMEZONE_OFFSET, DEFAULT_REMINDER_LEAD_MINUTES, DEFAULT_LOCK_MINUTES
from utils.embeds import make_embed, error_embed, success_embed, build_roster_embed
from utils.permissions import grant_group_access, revoke_group_access
from models import group as group_model, registration as reg_model
from database import get_config, get_channel_config


# ═══════════════════ HELPERS ═══════════════════

def get_today_event_id():
    utc_now = datetime.datetime.utcnow()
    local_now = utc_now + datetime.timedelta(hours=TIMEZONE_OFFSET)
    return local_now.strftime("%Y-%m-%d")


def parse_match_time(event_id: str, time_str: str) -> datetime.datetime:
    time_str = time_str.strip()
    offset_minutes = 0
    match = re.search(r'\(\+([0-9]+)m\)', time_str)
    if match:
        offset_minutes = int(match.group(1))
        time_str = re.sub(r'\s*\(\+[0-9]+m\)', '', time_str).strip()
    
    base_dt = None
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
        try:
            base_dt = datetime.datetime.strptime(time_str, fmt)
            break
        except ValueError:
            continue
            
    if not base_dt:
        raise ValueError(f"Could not parse time format for: '{time_str}'")
    
    event_date = datetime.datetime.strptime(event_id, "%Y-%m-%d")
    dt = datetime.datetime(
        year=event_date.year,
        month=event_date.month,
        day=event_date.day,
        hour=base_dt.hour,
        minute=base_dt.minute
    )
    dt += datetime.timedelta(minutes=offset_minutes)
    return dt


# ═══════════════════ CANCEL / RESCHEDULE VIEWS ═══════════════════

class CancelSlotView(ui.View):
    """View with Cancel Slot button, attached to reminder embeds."""
    
    def __init__(self, event_id, group_id):
        super().__init__(timeout=None)
        self.event_id = event_id
        self.group_id = group_id

    @ui.button(label="🚪 Cancel Slot", style=discord.ButtonStyle.danger, custom_id="cancel_slot_btn")
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

        # Check registration
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
            # Remove from owner and teammates
            teammate_ids = cancelled.get("teammate_ids", [])
            for tid in teammate_ids:
                member = guild.get_member(int(tid))
                if member and role:
                    await revoke_group_access(member, role)

        # Refresh roster
        updated_group = group_model.get_group(event_id, self.group_id)
        if updated_group:
            regs = reg_model.get_group_registrations(self.group_id, event_id)
            embed = build_roster_embed(updated_group, regs, updated_group["capacity"])
            
            ch = guild.get_channel(updated_group.get("channel_id"))
            if ch:
                msg_id = updated_group.get("roster_message_id")
                if msg_id:
                    try:
                        msg = await ch.fetch_message(msg_id)
                        await msg.edit(embed=embed)
                    except discord.NotFound:
                        pass

        team_name = cancelled.get("team_name", "your team")
        await interaction.followup.send(
            embed=success_embed(
                "✅ Slot Cancelled",
                f"{Theme.SEP}\n\n"
                f"Team **{team_name}** has been removed from Group **{self.group_id}**.\n\n{Theme.SEP}"
            ),
            ephemeral=True
        )

    @ui.button(label="🔄 Change Schedule", style=discord.ButtonStyle.primary, custom_id="change_schedule_btn")
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
            lock_min = int(get_config("lock_minutes", DEFAULT_LOCK_MINUTES))
            await interaction.response.send_message(
                embed=error_embed(
                    "⛔ Locked",
                    f"{Theme.SEP}\n\n"
                    f"Schedule changes are **locked** — less than {lock_min} minutes before match.\n"
                    f"Contact an admin to move you.\n\n{Theme.SEP}"
                ),
                ephemeral=True
            )
            return

        # Check registration
        reg = reg_model.get_registration(owner_id, event_id)
        if not reg or reg.get("group_id") != self.group_id:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", "You don't have a registration in this group."),
                ephemeral=True
            )
            return

        # Show open groups
        open_groups = group_model.get_open_groups(event_id)
        # Exclude current group
        open_groups = [g for g in open_groups if g["group_id"] != self.group_id]

        if not open_groups:
            await interaction.response.send_message(
                embed=error_embed(
                    "❌ No Available Groups",
                    f"{Theme.SEP}\n\nAll other groups are full or locked.\n\n{Theme.SEP}"
                ),
                ephemeral=True
            )
            return

        # Build select menu
        options = []
        for g in open_groups[:25]:  # Discord max 25 options
            gid = g["group_id"]
            count = g["current_count"]
            cap = g["capacity"]
            m1 = g.get("match1", {}).get("start", "TBD")
            options.append(
                discord.SelectOption(
                    label=f"Group {gid}",
                    description=f"{count}/{cap} filled │ M1: {m1}",
                    value=gid,
                    emoji="📍"
                )
            )

        view = GroupSelectView(event_id, self.group_id, options)
        await interaction.response.send_message(
            embed=make_embed(
                "🔄 Change Group",
                f"{Theme.SEP}\n\nSelect a new group from the dropdown below.\n\n{Theme.SEP}",
                Theme.ACCENT
            ),
            view=view,
            ephemeral=True
        )


class GroupSelectDropdown(ui.Select):
    """Dropdown for picking a new group."""
    
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
        reg_model.move_registration(owner_id, event_id, new_group_id)

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

        # Refresh both rosters
        for gid in [self.current_group_id, new_group_id]:
            g_doc = group_model.get_group(event_id, gid)
            if g_doc:
                regs = reg_model.get_group_registrations(gid, event_id)
                embed = build_roster_embed(g_doc, regs, g_doc["capacity"])
                ch = guild.get_channel(g_doc.get("channel_id"))
                if ch:
                    msg_id = g_doc.get("roster_message_id")
                    if msg_id:
                        try:
                            msg = await ch.fetch_message(msg_id)
                            await msg.edit(embed=embed)
                        except discord.NotFound:
                            pass

        await interaction.followup.send(
            embed=success_embed(
                "✅ Group Changed",
                f"{Theme.SEP}\n\n"
                f"Moved from **{self.current_group_id}** → **{new_group_id}**\n\n{Theme.SEP}"
            ),
            ephemeral=True
        )


class GroupSelectView(ui.View):
    def __init__(self, event_id, current_group_id, options):
        super().__init__(timeout=60)
        self.add_item(GroupSelectDropdown(event_id, current_group_id, options))


# ═══════════════════ SLOT LIST BUILDER ═══════════════════

def build_slot_list_embed(group_doc, registrations):
    """Build the frozen slot list published at T-20min lock."""
    group_id = group_doc.get("group_id", "????")
    m1 = group_doc.get("match1", {})
    m2 = group_doc.get("match2", {})

    lines = []
    for i, reg in enumerate(registrations, 1):
        team = reg.get("team_name", "???")
        captain = reg.get("owner_id", "")
        lines.append(f"**Slot {i:02d}** — {team} │ <@{captain}>")

    # Fill remaining as OPEN
    cap = group_doc.get("capacity", 21)
    for i in range(len(registrations) + 1, cap + 1):
        lines.append(f"**Slot {i:02d}** — *OPEN*")

    slot_text = "\n".join(lines)
    embed = make_embed(
        f"📋 SLOT LIST — GROUP {group_id}",
        f"{Theme.SEP}\n\n"
        f"**🎮 Match 1:** `{m1.get('start', 'TBD')}` │ Map `{m1.get('map', 'TBD')}`\n"
        f"**🎮 Match 2:** `{m2.get('start', 'TBD')}` │ Map `{m2.get('map', 'TBD')}`\n\n"
        f"{Theme.THIN_SEP}\n\n"
        f"{slot_text}\n\n"
        f"{Theme.THIN_SEP}\n"
        f"📌 **NO SS = NO POINTS**\n"
        f"📸 Submit screenshots in this channel after each match.\n\n{Theme.SEP}",
        Theme.GOLD,
        f"🔒 Frozen Roster — Group {group_id}"
    )
    return embed


# ═══════════════════ REMINDER COG ═══════════════════

class RemindersCog(commands.Cog):
    """Match reminders, auto-lock, and slot list publishing."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.reminder_loop.start()

    async def cog_unload(self):
        self.reminder_loop.cancel()

    async def send_reminder_embed(self, guild: discord.Guild, event_id: str, group_doc: dict):
        group_id = group_doc["group_id"]
        channel = guild.get_channel(group_doc.get("channel_id"))
        role = guild.get_role(group_doc.get("role_id"))

        if not channel:
            return False

        m1 = group_doc.get("match1", {})
        m2 = group_doc.get("match2", {})

        reminder_embed = make_embed(
            f"🚨 Match Reminder — Group {group_id}",
            f"{Theme.SEP}\n\n"
            f"⏱️ **Your matches are starting soon!**\n\n"
            f"╭── 🎮 **Match Schedule** ──╮\n"
            f"│\n"
            f"│  **Match 1:** IDP `{m1.get('idp', 'TBD')}` │ Start `{m1.get('start', 'TBD')}`\n"
            f"│  📍 Map: `{m1.get('map', 'TBD')}`\n"
            f"│\n"
            f"│  **Match 2:** IDP `{m2.get('idp', 'TBD')}` │ Start `{m2.get('start', 'TBD')}`\n"
            f"│  📍 Map: `{m2.get('map', 'TBD')}`\n"
            f"│\n"
            f"╰────────────────────────────╯\n\n"
            f"📌 **NO SS = NO POINTS** — Submit screenshots after each match!\n\n"
            f"{Theme.THIN_SEP}\n"
            f"🚪 Need to cancel? Use the button below.\n"
            f"🔄 Want a different time? Use Change Schedule.\n\n{Theme.SEP}",
            Theme.CRIMSON,
            "⚔️ Good luck, warriors!"
        )

        ping = role.mention if role else ""
        cancel_view = CancelSlotView(event_id, group_id)
        await channel.send(content=ping, embed=reminder_embed, view=cancel_view)
        group_model.set_reminder_sent(event_id, group_id)
        return True

    @tasks.loop(minutes=1)
    async def reminder_loop(self):
        """Check every minute for groups needing reminders or locking."""
        event_id = get_today_event_id()
        all_groups = group_model.get_all_groups(event_id)
        if not all_groups:
            return

        from config import GUILD_ID
        guild = None
        if GUILD_ID:
            guild = self.bot.get_guild(int(GUILD_ID))
        if not guild and self.bot.guilds:
            guild = self.bot.guilds[0]
            
        if not guild:
            return

        reminder_lead_min = int(get_config("reminder_lead_minutes", DEFAULT_REMINDER_LEAD_MINUTES))
        lock_min = int(get_config("lock_minutes", DEFAULT_LOCK_MINUTES))

        utc_now = datetime.datetime.utcnow()
        local_now = utc_now + datetime.timedelta(hours=TIMEZONE_OFFSET)

        for g in all_groups:
            # 1. Check reminders and locking based on Match 1
            m1_start = g.get("match1", {}).get("start")
            if m1_start and m1_start != "TBD":
                try:
                    m1_dt = parse_match_time(event_id, m1_start)
                    time_diff = m1_dt - local_now
                    diff_minutes = time_diff.total_seconds() / 60

                    # Match reminder
                    if 0 <= diff_minutes <= reminder_lead_min and not g.get("reminder_sent", False):
                        await self.send_reminder_embed(guild, event_id, g)
                        print(f"⏰ Auto-reminder sent for group {g['group_id']}", flush=True)

                    # Group locking
                    if 0 <= diff_minutes <= lock_min and not g.get("locked", False):
                        group_model.lock_group(event_id, g["group_id"])
                        channel = guild.get_channel(g.get("channel_id"))
                        if channel:
                            regs = reg_model.get_group_registrations(g["group_id"], event_id)
                            slot_embed = build_slot_list_embed(g, regs)
                            await channel.send(embed=slot_embed)
                            group_model.set_slot_list_published(event_id, g["group_id"])
                            print(f"🔒 Auto-locked group {g['group_id']} and published slot list", flush=True)

                except Exception as e:
                    print(f"Error checking reminder/lock for group {g['group_id']}: {e}", flush=True)

            # 2. Check no-show tracking based on Match 2 end (Match 2 start + 60 minutes)
            m2_start = g.get("match2", {}).get("start")
            if m2_start and m2_start != "TBD" and not g.get("noshow_check_done", False):
                try:
                    m2_dt = parse_match_time(event_id, m2_start)
                    deadline_dt = m2_dt + datetime.timedelta(minutes=60)
                    
                    if local_now >= deadline_dt:
                        from database import groups as groups_collection
                        groups_collection.update_one(
                            {"event_id": event_id, "group_id": g["group_id"]},
                            {"$set": {"noshow_check_done": True}}
                        )
                        
                        regs = reg_model.get_group_registrations(g["group_id"], event_id)
                        log_ch_id = get_channel_config("admin_log")
                        log_ch = guild.get_channel(log_ch_id) if log_ch_id else None
                        
                        for reg in regs:
                            if not reg.get("ss_submitted", False):
                                reg_model.mark_no_show(reg["owner_id"], event_id)
                                
                                channel = guild.get_channel(g.get("channel_id"))
                                if channel:
                                    noshow_embed = error_embed(
                                        "⚠️ No-Show Flagged",
                                        f"Team **{reg['team_name']}** (<@{reg['owner_id']}>) failed to submit a screenshot within 30 minutes of match end and has been flagged as a no-show."
                                    )
                                    await channel.send(embed=noshow_embed)
                                
                                if log_ch:
                                    admin_log_embed = make_embed(
                                        "⚠️ Team Flagged as No-Show",
                                        f"{Theme.SEP}\n\n"
                                        f"👤 **Captain:** <@{reg['owner_id']}>\n"
                                        f"🏷️ **Team:** `{reg['team_name']}`\n"
                                        f"📍 **Group:** `{g['group_id']}`\n"
                                        f"📅 **Event:** `{event_id}`\n\n{Theme.SEP}",
                                        Theme.WARNING
                                    )
                                    await log_ch.send(admin_log_embed)
                                    
                        print(f"🧹 No-show check completed for group {g['group_id']}", flush=True)

                except Exception as e:
                    print(f"Error checking no-shows for group {g['group_id']}: {e}", flush=True)

    @reminder_loop.before_loop
    async def before_reminder_loop(self):
        await self.bot.wait_until_ready()

    # ─────────────── MANUAL REMINDER COMMAND ───────────────

    @app_commands.command(
        name="remind",
        description="[Admin] Send a match reminder to a specific group"
    )
    @app_commands.describe(group_id="Group ID (e.g. G0001)")
    @app_commands.checks.has_permissions(administrator=True)
    async def remind_group(self, interaction: discord.Interaction, group_id: str):
        event_id = get_today_event_id()
        group_doc = group_model.get_group(event_id, group_id.upper())

        if not group_doc:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Group `{group_id}` not found for today."),
                ephemeral=True
            )
            return

        guild = interaction.guild
        success = await self.send_reminder_embed(guild, event_id, group_doc)

        if success:
            await interaction.response.send_message(
                embed=success_embed("✅ Reminder Sent", f"Reminder posted to Group {group_id.upper()}."),
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=error_embed("❌ Channel Missing", "The group channel was deleted or is inaccessible."),
                ephemeral=True
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        
        if not message.attachments:
            return

        event_id = get_today_event_id()
        
        from database import groups as groups_collection
        group_doc = groups_collection.find_one({
            "event_id": event_id,
            "channel_id": message.channel.id,
            "archived": {"$ne": True}
        })
        if not group_doc:
            return
            
        sender_id = str(message.author.id)
        from database import registrations as regs_collection
        reg = regs_collection.find_one({
            "event_id": event_id,
            "group_id": group_doc["group_id"],
            "status": "registered",
            "$or": [
                {"owner_id": sender_id},
                {"teammate_ids": sender_id}
            ]
        })
        if not reg:
            return
            
        if not reg.get("ss_submitted", False):
            reg_model.mark_ss_submitted(reg["owner_id"], event_id)
            
            embed = success_embed(
                "📸 Screenshot Received",
                f"Thank you {message.author.mention}, screenshot recorded for team **{reg['team_name']}**."
            )
            await message.channel.send(embed=embed, delete_after=10)

    # ─────────────── LOCK GROUP ───────────────

    @app_commands.command(
        name="lockgroup",
        description="[Admin] Lock a group — prevents cancel/reschedule and publishes slot list"
    )
    @app_commands.describe(group_id="Group ID (e.g. G0001)")
    @app_commands.checks.has_permissions(administrator=True)
    async def lock_group_cmd(self, interaction: discord.Interaction, group_id: str):
        event_id = get_today_event_id()
        gid = group_id.upper()
        group_doc = group_model.get_group(event_id, gid)

        if not group_doc:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Group `{gid}` not found."),
                ephemeral=True
            )
            return

        await interaction.response.defer()

        # Lock the group
        group_model.lock_group(event_id, gid)

        # Publish frozen slot list
        guild = interaction.guild
        channel = guild.get_channel(group_doc.get("channel_id"))
        if channel:
            regs = reg_model.get_group_registrations(gid, event_id)
            slot_embed = build_slot_list_embed(group_doc, regs)
            await channel.send(embed=slot_embed)
            group_model.set_slot_list_published(event_id, gid)

        await interaction.followup.send(
            embed=success_embed(
                "🔒 Group Locked",
                f"Group **{gid}** is now locked.\n"
                f"Cancel/reschedule disabled. Slot list published."
            )
        )

    # ─────────────── PUBLISH SLOT LIST ───────────────

    @app_commands.command(
        name="slotlist",
        description="[Admin] Publish the slot list for a group"
    )
    @app_commands.describe(group_id="Group ID (e.g. G0001)")
    @app_commands.checks.has_permissions(administrator=True)
    async def publish_slot_list(self, interaction: discord.Interaction, group_id: str):
        event_id = get_today_event_id()
        gid = group_id.upper()
        group_doc = group_model.get_group(event_id, gid)

        if not group_doc:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Group `{gid}` not found."),
                ephemeral=True
            )
            return

        guild = interaction.guild
        channel = guild.get_channel(group_doc.get("channel_id"))
        if not channel:
            await interaction.response.send_message(
                embed=error_embed("❌ Channel Missing", "Group channel not found."),
                ephemeral=True
            )
            return

        regs = reg_model.get_group_registrations(gid, event_id)
        slot_embed = build_slot_list_embed(group_doc, regs)
        await channel.send(embed=slot_embed)

        await interaction.response.send_message(
            embed=success_embed("✅ Slot List Published", f"Slot list posted in Group {gid}."),
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(RemindersCog(bot))
