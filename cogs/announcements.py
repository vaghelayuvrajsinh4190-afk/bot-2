"""
Mack Bot — Announcements Cog
Announce, DM broadcast, room credentials — carried over from the old bot.
"""

import asyncio
import discord
from discord.ext import commands
from discord import app_commands

from config import Theme
from utils.embeds import make_embed, error_embed, success_embed
from database import get_channel_config


class AnnouncementsCog(commands.Cog):
    """Announcement, DM broadcast, and room credential commands."""

    def __init__(self, bot):
        self.bot = bot

    # ─────────────── ANNOUNCE ───────────────

    @app_commands.command(name="announce", description="[Admin] Send an announcement to a channel")
    @app_commands.describe(channel="Channel to send to", message="Announcement text")
    @app_commands.checks.has_permissions(administrator=True)
    async def announce(self, interaction: discord.Interaction, channel: discord.TextChannel, message: str):
        embed = make_embed(
            "📣 Announcement",
            f"{Theme.SEP}\n\n{message}\n\n{Theme.SEP}",
            Theme.GOLD,
            f"Posted by {interaction.user.display_name}"
        )
        await channel.send(embed=embed)
        await interaction.response.send_message(
            embed=success_embed("✅ Sent", f"Announcement delivered to {channel.mention}"),
            ephemeral=True
        )

    # ─────────────── ROOM CREDENTIALS ───────────────

    @app_commands.command(name="room", description="[Admin] Post room ID and password to a group channel")
    @app_commands.describe(
        group_id="Group ID (e.g. G0001)",
        room_id="Room/Lobby ID",
        password="Room password",
        custom_message="Optional extra message"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def room_cmd(
        self, interaction: discord.Interaction,
        group_id: str, room_id: str, password: str,
        custom_message: str = None
    ):
        from models import group as group_model
        from cogs.reminders import get_today_event_id

        event_id = get_today_event_id()
        gid = group_id.upper()
        group_doc = await asyncio.to_thread(group_model.get_group, event_id, gid)

        if not group_doc:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Group `{gid}` not found for today."),
                ephemeral=True
            )
            return

        channel = interaction.guild.get_channel(group_doc.get("channel_id"))
        role = interaction.guild.get_role(group_doc.get("role_id"))

        if not channel:
            await interaction.response.send_message(
                embed=error_embed("❌ Channel Missing", "Group channel not found."),
                ephemeral=True
            )
            return

        desc = (
            f"{Theme.SEP}\n\n"
            f"⚠️ **CONFIDENTIAL** — Do NOT share outside this channel!\n\n"
        )
        if custom_message:
            desc += f"📝 {custom_message}\n\n"

        embed = make_embed(
            f"🔐 Room Credentials — Group {gid}",
            desc,
            Theme.SUCCESS,
            f"Posted by {interaction.user.display_name}"
        )
        embed.add_field(name="🆔 Room ID", value=f"```fix\n{room_id}\n```", inline=True)
        embed.add_field(name="🔒 Password", value=f"```fix\n{password}\n```", inline=True)

        ping = role.mention if role else ""
        await channel.send(content=ping, embed=embed)
        await interaction.response.send_message(
            embed=success_embed("✅ Room Details Sent", f"Posted to Group {gid}."),
            ephemeral=True
        )

    # ─────────────── DM BROADCAST ───────────────

    @app_commands.command(name="dm", description="[Admin] DM specific members")
    @app_commands.describe(members="Mention the members to DM", message="Message to send")
    @app_commands.checks.has_permissions(administrator=True)
    async def dm_members(self, interaction: discord.Interaction, members: str, message: str):
        await interaction.response.defer(ephemeral=True)

        # Parse member mentions from the string
        guild = interaction.guild
        member_ids = [m.strip("<@!>") for m in members.split() if m.startswith("<@")]
        
        if not member_ids:
            await interaction.followup.send(
                embed=error_embed("⚠️ No Members", "Mention at least one member (e.g. @user)."),
                ephemeral=True
            )
            return

        success = []
        failed = []
        for mid in member_ids:
            try:
                member = guild.get_member(int(mid))
                if member:
                    dm_embed = make_embed(
                        "📩 Message from Admin",
                        f"{Theme.SEP}\n\n{message}\n\n{Theme.SEP}",
                        Theme.PREMIUM,
                        f"Sent by {interaction.user.display_name}"
                    )
                    await member.send(embed=dm_embed)
                    success.append(f"<@{mid}>")
                else:
                    failed.append(f"<@{mid}>")
            except Exception:
                failed.append(f"<@{mid}>")

        desc = f"{Theme.SEP}\n\n**📝 Message:**\n> {message}\n\n{Theme.THIN_SEP}\n\n"
        if success:
            desc += f"**✅ Delivered ({len(success)}):** {', '.join(success)}\n"
        if failed:
            desc += f"**❌ Failed ({len(failed)}):** {', '.join(failed)}\n"
        desc += f"\n{Theme.SEP}"

        color = Theme.SUCCESS if not failed else Theme.WARNING
        await interaction.followup.send(
            embed=make_embed(f"📨 DM — {len(success)}/{len(success)+len(failed)} Delivered", desc, color),
            ephemeral=True
        )

    # ─────────────── DM ALL ───────────────

    @app_commands.command(name="dmall", description="[Admin] DM all server members")
    @app_commands.describe(message="Message to broadcast")
    @app_commands.checks.has_permissions(administrator=True)
    async def dm_all(self, interaction: discord.Interaction, message: str):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        members = [m for m in guild.members if not m.bot]
        total = len(members)

        if total == 0:
            await interaction.followup.send(
                embed=error_embed("⚠️ No Members", "No human members found."),
                ephemeral=True
            )
            return

        success = 0
        failed = 0
        for member in members:
            try:
                dm_embed = make_embed(
                    "📩 Server Announcement",
                    f"{Theme.SEP}\n\n{message}\n\n{Theme.SEP}",
                    Theme.PREMIUM,
                    f"From {guild.name}"
                )
                await member.send(embed=dm_embed)
                success += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.5)  # Rate limit safety

        color = Theme.SUCCESS if failed == 0 else Theme.WARNING
        await interaction.followup.send(
            embed=make_embed(
                f"📨 Broadcast Complete — {success}/{total}",
                f"{Theme.SEP}\n\n"
                f"**✅ Delivered:** `{success}`\n"
                f"**❌ Failed:** `{failed}`\n"
                f"**👥 Total:** `{total}`\n\n{Theme.SEP}",
                color
            ),
            ephemeral=True
        )

    # ─────────────── CLEAR MESSAGES ───────────────

    @app_commands.command(name="clear", description="[Admin] Purge messages in the current channel")
    @app_commands.describe(amount="Number of messages to delete (max 100)")
    @app_commands.checks.has_permissions(administrator=True)
    async def clear(self, interaction: discord.Interaction, amount: int = 10):
        if amount > 100:
            amount = 100
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(limit=amount)
            await interaction.followup.send(
                embed=success_embed("🧹 Cleared", f"Removed **{len(deleted)}** messages."),
                ephemeral=True
            )
        except Exception as err:
            await interaction.followup.send(
                embed=error_embed("❌ Failed", f"`{err}`"),
                ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(AnnouncementsCog(bot))
