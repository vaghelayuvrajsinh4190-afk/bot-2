"""
Mack Bot — Tier Management Cog
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Cross-tier workflow automation for team promotions and demotions.

Provides admin commands to manually promote or demote teams across
scrim tiers based on leaderboard performance. Updates the global team
registry and announces the movement in the configured channels.
"""

import discord
from discord.ext import commands
from discord import app_commands
import datetime

from models import global_teams
from database import get_channel_config
from utils.embeds import make_embed, Theme, success_embed, error_embed

class TierManagement(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _log_tier_change(self, interaction: discord.Interaction, title: str, description: str):
        """Log tier changes to the admin log channel."""
        log_channel_id = get_channel_config("admin_log")
        if not log_channel_id:
            return
            
        channel = self.bot.get_channel(log_channel_id)
        if not channel:
            return
            
        embed = make_embed(
            title,
            f"{description}\n\n**Admin:** {interaction.user.mention}",
            Theme.WARNING
        )
        await channel.send(embed=embed)

    # ═══════════════════ COMMANDS ═══════════════════

    tier_group = app_commands.Group(name="tier", description="Tier management and promotion tools")

    @tier_group.command(name="promote", description="Promote a team to a higher tier")
    @app_commands.describe(
        owner="The team captain/owner",
        from_tier="Current tier (e.g. T3)",
        to_tier="New tier (e.g. T2)"
    )
    @app_commands.default_permissions(administrator=True)
    async def promote_team_cmd(
        self, interaction: discord.Interaction, owner: discord.Member, from_tier: str, to_tier: str
    ):
        await interaction.response.defer(ephemeral=True)
        
        from_tier = from_tier.upper()
        to_tier = to_tier.upper()
        
        team_doc = global_teams.get_team(str(owner.id))
        if not team_doc:
            await interaction.followup.send(embed=error_embed("❌ Not Found", "Team not found in global tracking."))
            return
            
        if team_doc.get("current_tier") != from_tier:
            await interaction.followup.send(embed=error_embed("❌ Mismatch", f"Team is currently in {team_doc.get('current_tier')}, not {from_tier}."))
            return
            
        global_teams.promote_team(str(owner.id), to_tier)
        
        # We would also ideally grant a Discord role here if using role-based access
        # For now, we log the change.
        team_name = team_doc.get("team_name")
        await self._log_tier_change(
            interaction,
            "📈 Team Promoted",
            f"**Team:** {team_name}\n**Captain:** <@{owner.id}>\n**Path:** `{from_tier}` ➔ `{to_tier}`"
        )
        
        await interaction.followup.send(
            embed=success_embed("✅ Promoted", f"**{team_name}** has been promoted to **{to_tier}**.")
        )


    @tier_group.command(name="demote", description="Demote a team to a lower tier")
    @app_commands.describe(
        owner="The team captain/owner",
        from_tier="Current tier (e.g. T2)",
        to_tier="New tier (e.g. T3)"
    )
    @app_commands.default_permissions(administrator=True)
    async def demote_team_cmd(
        self, interaction: discord.Interaction, owner: discord.Member, from_tier: str, to_tier: str
    ):
        await interaction.response.defer(ephemeral=True)
        
        from_tier = from_tier.upper()
        to_tier = to_tier.upper()
        
        team_doc = global_teams.get_team(str(owner.id))
        if not team_doc:
            await interaction.followup.send(embed=error_embed("❌ Not Found", "Team not found in global tracking."))
            return
            
        if team_doc.get("current_tier") != from_tier:
            await interaction.followup.send(embed=error_embed("❌ Mismatch", f"Team is currently in {team_doc.get('current_tier')}, not {from_tier}."))
            return
            
        global_teams.demote_team(str(owner.id), to_tier)
        
        team_name = team_doc.get("team_name")
        await self._log_tier_change(
            interaction,
            "📉 Team Demoted",
            f"**Team:** {team_name}\n**Captain:** <@{owner.id}>\n**Path:** `{from_tier}` ➔ `{to_tier}`"
        )
        
        await interaction.followup.send(
            embed=success_embed("✅ Demoted", f"**{team_name}** has been demoted to **{to_tier}**.")
        )


    @tier_group.command(name="review", description="Review candidates for promotion and demotion")
    @app_commands.describe(tier="The tier to review (e.g. T3 for top T3s, T2 for bottom T2s)")
    @app_commands.default_permissions(administrator=True)
    async def tier_review(self, interaction: discord.Interaction, tier: str):
        await interaction.response.defer()
        
        tier = tier.upper()
        promotions = global_teams.get_promotion_candidates(tier, limit=5)
        demotions = global_teams.get_demotion_candidates(tier, limit=5)
        
        promo_text = ""
        for i, t in enumerate(promotions, 1):
            promo_text += f"`{i}.` **{t['team_name']}** — `{t.get('total_points', 0)}` pts\n"
            
        demo_text = ""
        for i, t in enumerate(demotions, 1):
            demo_text += f"`{i}.` **{t['team_name']}** — `{t.get('total_points', 0)}` pts (Strikes: `{t.get('no_shows', 0)}`)\n"
            
        if not promo_text: promo_text = "*No candidates.*"
        if not demo_text: demo_text = "*No candidates.*"
        
        embed = make_embed(
            f"📋 Tier Review: {tier}",
            f"Reviewing the top candidates for promotion out of {tier}, and bottom candidates for demotion out of {tier}.",
            Theme.INFO
        )
        embed.add_field(name="📈 Top Candidates (Promotion)", value=promo_text, inline=False)
        embed.add_field(name="📉 Bottom Candidates (Demotion)", value=demo_text, inline=False)
        
        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(TierManagement(bot))
