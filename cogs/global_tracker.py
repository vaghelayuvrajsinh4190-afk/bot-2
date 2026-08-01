"""
Mack Bot — Global Tracker Cog
Handles the cross-tier global leaderboard and team stats.
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import datetime

from models import global_teams
from database import get_channel_config
from utils.embeds import make_embed, Theme, success_embed, error_embed
from utils.permissions import admin_only

class GlobalTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.update_leaderboard_task.start()

    def cog_unload(self):
        self.update_leaderboard_task.cancel()

    @tasks.loop(minutes=30)
    async def update_leaderboard_task(self):
        """Periodically update the global leaderboard channel."""
        try:
            channel_id = get_channel_config("leaderboard")
            if not channel_id:
                return

            channel = self.bot.get_channel(channel_id)
            if not channel:
                return

            # Purge old messages (or we could just edit a single one)
            async for msg in channel.history(limit=50):
                if msg.author == self.bot.user:
                    try:
                        await msg.delete()
                    except:
                        pass

            # Build and send the new embed
            from utils.embeds import build_global_leaderboard_embed
            teams = global_teams.get_leaderboard(limit=50)
            
            # Split into T3 and T2 (assuming T1 later)
            t3_teams = [t for t in teams if t.get("current_tier") == "T3"]
            t2_teams = [t for t in teams if t.get("current_tier") == "T2"]

            if t2_teams:
                embed_t2 = build_global_leaderboard_embed(t2_teams, tier_filter="T2")
                await channel.send(embed=embed_t2)
            
            embed_t3 = build_global_leaderboard_embed(t3_teams, tier_filter="T3")
            await channel.send(embed=embed_t3)
            
        except Exception as e:
            print(f"Error updating global leaderboard: {e}", flush=True)

    @update_leaderboard_task.before_loop
    async def before_update(self):
        await self.bot.wait_until_ready()

    # ═══════════════════ COMMANDS ═══════════════════

    global_group = app_commands.Group(name="global", description="Global team tracking and leaderboards")

    @global_group.command(name="leaderboard", description="View the global leaderboard")
    @app_commands.describe(tier="Filter by tier (e.g., T3, T2)")
    async def view_leaderboard(self, interaction: discord.Interaction, tier: str = None):
        """View the global leaderboard."""
        await interaction.response.defer()
        
        tier = tier.upper() if tier else None
        teams = global_teams.get_leaderboard(tier=tier, limit=25)
        
        if not teams:
            await interaction.followup.send(
                embed=error_embed("📭 No Teams Found", f"No teams found{' in ' + tier if tier else ' on the global leaderboard'} yet.")
            )
            return

        from utils.embeds import build_global_leaderboard_embed
        embed = build_global_leaderboard_embed(teams, tier_filter=tier)
        await interaction.followup.send(embed=embed)


    @global_group.command(name="stats", description="View global stats for a team")
    @app_commands.describe(team_name="Exact name of the team to look up")
    async def view_team_stats(self, interaction: discord.Interaction, team_name: str):
        """View a specific team's cross-tier stats."""
        await interaction.response.defer()
        
        team_doc = global_teams.get_team_by_name(team_name)
        if not team_doc:
            await interaction.followup.send(
                embed=error_embed("❌ Not Found", f"Could not find any global stats for team **{team_name}**.")
            )
            return
            
        from utils.embeds import build_team_stats_embed
        embed = build_team_stats_embed(team_doc)
        await interaction.followup.send(embed=embed)


    @global_group.command(name="history", description="View a player's participation history")
    @app_commands.describe(user="The user to look up")
    async def view_player_history(self, interaction: discord.Interaction, user: discord.Member):
        """View a user's team ownership history."""
        await interaction.response.defer()
        
        team_doc = global_teams.get_team(str(user.id))
        
        if not team_doc:
            await interaction.followup.send(
                embed=error_embed("📭 No History", f"{user.mention} is not registered as a team owner in the global system.")
            )
            return
            
        from utils.embeds import build_team_stats_embed
        embed = build_team_stats_embed(team_doc)
        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(GlobalTracker(bot))
