"""
Mack Bot — Points & Leaderboard Cog
Slash commands for setpoints, setposition, addresult, matchresults, leaderboard, mvp, resetresults, and postleaderboard.
"""

import datetime
import re
import discord
from discord.ext import commands
from discord import app_commands

from config import Theme, DEFAULT_POSITION_POINTS, get_rank_emoji, TIMEZONE_OFFSET
from database import get_config, set_config, get_channel_config, match_results as results_collection
from utils.embeds import make_embed, error_embed, success_embed


def get_today_event_id():
    utc_now = datetime.datetime.utcnow()
    local_now = utc_now + datetime.timedelta(hours=TIMEZONE_OFFSET)
    return local_now.strftime("%Y-%m-%d")


class PointsCog(commands.Cog):
    """Cog for managing scores, match results, and tournament standings."""

    def __init__(self, bot):
        self.bot = bot

    # ─────────────── CONFIG COMMANDS ───────────────

    @app_commands.command(name="setpoints", description="[Admin] View or set the number of points per kill")
    @app_commands.describe(kill_points="Points awarded per kill (0 or positive)")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_points(self, interaction: discord.Interaction, kill_points: int = None):
        if kill_points is None:
            kp = get_config("kill_points", 1)
            pp = get_config("position_points", DEFAULT_POSITION_POINTS)
            pos_lines = []
            for pos in sorted(pp.keys(), key=lambda x: int(x)):
                pts = pp[pos]
                if pts > 0:
                    medal = get_rank_emoji(int(pos))
                    pos_lines.append(f"> {medal} Position **#{pos}** → `{pts}` pts")
            pos_str = "\n".join(pos_lines) if pos_lines else "> No position points set"
            embed = make_embed(
                "🏅 Current Points System",
                f"{Theme.SEP}\n\n**💀 Kill Points:** `{kp}` per kill\n\n**🏆 Position Points:**\n{pos_str}\n\n"
                f"{Theme.THIN_SEP}\n*Use `/setpoints <kill_pts>` to change kill points.*\n"
                f"*Use `/setposition <pos> <pts>` to change position points.*",
                Theme.GOLD
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if kill_points < 0:
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid", "Kill points must be 0 or positive."),
                ephemeral=True
            )
            return

        set_config("kill_points", kill_points)
        embed = make_embed(
            "✅ Kill Points Updated",
            f"{Theme.SEP}\n\n**💀 Kill Points:** `{kill_points}` per kill\n\n{Theme.SEP}",
            Theme.SUCCESS
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="setposition", description="[Admin] Set position points for a placement rank")
    @app_commands.describe(
        position="Placement position rank (1-16)",
        points="Points awarded for this position (0 or positive)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def set_position(self, interaction: discord.Interaction, position: int, points: int):
        if position < 1 or position > 16:
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid Position", "Position must be between 1 and 16."),
                ephemeral=True
            )
            return
        if points < 0:
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid Points", "Points must be 0 or positive."),
                ephemeral=True
            )
            return

        current_pp = get_config("position_points", DEFAULT_POSITION_POINTS).copy()
        current_pp[str(position)] = points
        set_config("position_points", current_pp)

        medal = get_rank_emoji(position)
        embed = make_embed(
            "✅ Position Points Updated",
            f"{Theme.SEP}\n\n{medal} Position **#{position}** → `{points}` pts\n\n{Theme.SEP}",
            Theme.SUCCESS
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ─────────────── RESULT COMMANDS ───────────────

    @app_commands.command(name="addresult", description="[Admin] Record results for a team in a group match")
    @app_commands.describe(
        group_id="Group ID (e.g. G0001)",
        match_number="Match number (1 or 2)",
        team_name="Registered team name",
        kills="Number of kills",
        position="Placement position (1 to 16)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def add_result(
        self,
        interaction: discord.Interaction,
        group_id: str,
        match_number: int,
        team_name: str,
        kills: int,
        position: int
    ):
        if match_number not in (1, 2):
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid Match", "Match number must be 1 or 2."),
                ephemeral=True
            )
            return
        if position < 1 or position > 16:
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid Position", "Position must be between 1 and 16."),
                ephemeral=True
            )
            return
        if kills < 0:
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid Kills", "Kills must be 0 or positive."),
                ephemeral=True
            )
            return

        event_id = get_today_event_id()
        gid = group_id.upper()

        # Check if group exists for today
        from models import group as group_model
        group_doc = group_model.get_group(event_id, gid)
        if not group_doc:
            await interaction.response.send_message(
                embed=error_embed("❌ Group Not Found", f"Group `{gid}` not found for today."),
                ephemeral=True
            )
            return

        # Look up registered team details
        from database import registrations as regs_collection
        reg = regs_collection.find_one({
            "event_id": event_id,
            "group_id": gid,
            "status": "registered",
            "team_name": {"$regex": f"^{re.escape(team_name.strip())}$", "$options": "i"}
        })

        if reg:
            actual_team_name = reg["team_name"]
            owner_id = reg["owner_id"]
        else:
            actual_team_name = team_name.strip()
            owner_id = None

        kp_val = get_config("kill_points", 1)
        pp_dict = get_config("position_points", DEFAULT_POSITION_POINTS)

        kill_pts = kp_val * kills
        pos_pts = int(pp_dict.get(str(position), 0))
        total_pts = kill_pts + pos_pts

        team_key = actual_team_name.strip().lower()

        results_collection.update_one(
            {
                "event_id": event_id,
                "group_id": gid,
                "match_number": match_number,
                "team_key": team_key
            },
            {
                "$set": {
                    "event_id": event_id,
                    "group_id": gid,
                    "match_number": match_number,
                    "team_name": actual_team_name,
                    "team_key": team_key,
                    "owner_id": owner_id,
                    "kills": kills,
                    "position": position,
                    "position_points": pos_pts,
                    "kill_points": kill_pts,
                    "total_points": total_pts,
                    "recorded_at": datetime.datetime.utcnow().isoformat()
                }
            },
            upsert=True
        )

        medal = get_rank_emoji(position)
        embed = make_embed(
            f"✅ Result Added — Group {gid} Match {match_number}",
            f"{Theme.SEP}\n\n"
            f"**🏷️ Team:** {actual_team_name}\n"
            f"{medal} **Position:** #{position} → `{pos_pts}` pts\n"
            f"💀 **Kills:** {kills} → `{kill_pts}` pts\n\n"
            f"**🏆 Total:** `{total_pts}` points\n\n{Theme.SEP}",
            Theme.SUCCESS
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="matchresults", description="Display scores for a specific group match")
    @app_commands.describe(group_id="Group ID (e.g. G0001)", match_number="Match number (1 or 2)")
    async def match_results(self, interaction: discord.Interaction, group_id: str, match_number: int):
        if match_number not in (1, 2):
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid Match", "Match number must be 1 or 2."),
                ephemeral=True
            )
            return

        event_id = get_today_event_id()
        gid = group_id.upper()

        results = list(results_collection.find({
            "event_id": event_id,
            "group_id": gid,
            "match_number": match_number
        }).sort("position", 1))

        if not results:
            await interaction.response.send_message(
                embed=error_embed("❌ No Results", f"No results recorded for Group **{gid}** Match **{match_number}**."),
                ephemeral=True
            )
            return

        lines = []
        for r in results:
            pos = r.get("position", "?")
            medal = get_rank_emoji(pos) if isinstance(pos, int) else f"`{pos}.`"
            team = r.get("team_name", "?")
            kills = r.get("kills", 0)
            total = r.get("total_points", 0)
            lines.append(f"{medal} **{team}** — 💀 `{kills}` kills — 🏆 `{total}` pts")

        total_kills = sum(r.get("kills", 0) for r in results)
        embed = make_embed(
            f"📊 Group {gid} Match {match_number} ─ Results",
            f"{Theme.SEP}\n\n" + "\n".join(lines) + f"\n\n{Theme.THIN_SEP}\n"
            f"**Teams Recorded:** `{len(results)}` │ **Total Kills:** `{total_kills}`\n\n{Theme.SEP}",
            Theme.GOLD,
            f"Results for Group {gid} Match {match_number}"
        )
        await interaction.response.send_message(embed=embed)

    # ─────────────── STANDINGS & LEADERBOARD ───────────────

    @app_commands.command(name="leaderboard", description="View overall standings for today's event or a past day")
    @app_commands.describe(event_id="Date of event (YYYY-MM-DD), defaults to today")
    async def leaderboard(self, interaction: discord.Interaction, event_id: str = None):
        target_event = event_id or get_today_event_id()

        results = list(results_collection.find({"event_id": target_event}))
        if not results:
            await interaction.response.send_message(
                embed=error_embed("❌ No Results", f"No match results recorded for event `{target_event}`."),
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
                    "best_position": 99,
                }
            team_totals[tk]["total_kills"] += r.get("kills", 0)
            team_totals[tk]["total_points"] += r.get("total_points", 0)
            team_totals[tk]["matches_played"] += 1
            pos = r.get("position", 99)
            if pos < team_totals[tk]["best_position"]:
                team_totals[tk]["best_position"] = pos

        sorted_teams = sorted(team_totals.values(), key=lambda x: (x["total_points"], x["total_kills"]), reverse=True)

        lines = []
        for rank, t in enumerate(sorted_teams[:20], 1):
            medal = get_rank_emoji(rank)
            name = t["team_name"]
            if len(name) > 18:
                name = name[:16] + ".."
            lines.append(
                f"{medal} **{name}** ─ `{t['total_points']}` pts │ 💀 `{t['total_kills']}` kills │ "
                f"🎮 `{t['matches_played']}` matches │ Best: `#{t['best_position']}`"
            )

        podium = ""
        if len(sorted_teams) >= 3:
            podium = (
                f"╭── 👑 **Top 3 Teams** ──╮\n"
                f"│\n"
                f"│  🥇 **{sorted_teams[0]['team_name']}** ─ `{sorted_teams[0]['total_points']}` pts\n"
                f"│  🥈 **{sorted_teams[1]['team_name']}** ─ `{sorted_teams[1]['total_points']}` pts\n"
                f"│  🥉 **{sorted_teams[2]['team_name']}** ─ `{sorted_teams[2]['total_points']}` pts\n"
                f"│\n"
                f"╰───────────────────────╯\n"
            )
        elif len(sorted_teams) >= 1:
            podium = (
                f"╭── 👑 **Top Team** ──╮\n"
                f"│\n"
                f"│  🥇 **{sorted_teams[0]['team_name']}** ─ `{sorted_teams[0]['total_points']}` pts\n"
                f"│\n"
                f"╰─────────────────────╯\n"
            )

        embed = make_embed(
            f"🏆 Tournament Standings ─ {target_event}",
            f"{Theme.SEP}\n\n{podium}\n{Theme.THIN_SEP}\n\n" + "\n".join(lines) +
            f"\n\n{Theme.THIN_SEP}\n**📊 Stats:** `{len(sorted_teams)}` teams │ `{len(set(r.get('group_id') for r in results))}` groups\n\n{Theme.SEP}",
            Theme.GOLD,
            f"Overall Standing — {target_event}"
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="mvp", description="Show top players/teams with the highest kills")
    @app_commands.describe(group_id="Optional Group ID (e.g. G0001)")
    async def mvp(self, interaction: discord.Interaction, group_id: str = None):
        event_id = get_today_event_id()
        query = {"event_id": event_id}
        if group_id:
            query["group_id"] = group_id.upper()

        results = list(results_collection.find(query))
        if not results:
            scope_str = f"in Group {group_id.upper()}" if group_id else "today"
            await interaction.response.send_message(
                embed=error_embed("❌ No Results", f"No results recorded {scope_str} yet."),
                ephemeral=True
            )
            return

        team_kills = {}
        for r in results:
            tk = r.get("team_key") or r.get("team_name", "").strip().lower()
            if not tk:
                continue
            if tk not in team_kills:
                team_kills[tk] = {
                    "team_name": r.get("team_name", "?"),
                    "total_kills": 0,
                    "matches": 0,
                    "total_points": 0,
                    "best_pos": 99
                }
            team_kills[tk]["total_kills"] += r.get("kills", 0)
            team_kills[tk]["matches"] += 1
            team_kills[tk]["total_points"] += r.get("total_points", 0)
            pos = r.get("position", 99)
            if pos < team_kills[tk]["best_pos"]:
                team_kills[tk]["best_pos"] = pos

        top = max(team_kills.values(), key=lambda x: x["total_kills"])

        embed = make_embed(
            f"⭐ MVP ─ Today's Scrims" if not group_id else f"⭐ MVP ─ Group {group_id.upper()}",
            f"{Theme.SEP}\n\n"
            f"╭── 🏆 **Overall MVP** ──╮\n"
            f"│\n"
            f"│  🏷️ **Team:** `{top['team_name']}`\n"
            f"│  💀 **Total Kills:** `{top['total_kills']}`\n"
            f"│  🎮 **Matches:** `{top['matches']}`\n"
            f"│  📊 **Avg Kills:** `{top['total_kills'] / top['matches']:.1f}`\n"
            f"│  🏆 **Total Points:** `{top['total_points']}`\n"
            f"│\n"
            f"╰──────────────────────╯\n\n{Theme.SEP}",
            Theme.GOLD,
            "Overall Tournament MVP"
        )
        await interaction.response.send_message(embed=embed)

    # ─────────────── RESET COMMANDS ───────────────

    @app_commands.command(name="resetresults", description="[Admin] Reset scores for a specific group match")
    @app_commands.describe(group_id="Group ID (e.g. G0001)", match_number="Match number (1 or 2)")
    @app_commands.checks.has_permissions(administrator=True)
    async def reset_results(self, interaction: discord.Interaction, group_id: str, match_number: int):
        if match_number not in (1, 2):
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid Match", "Match number must be 1 or 2."),
                ephemeral=True
            )
            return

        event_id = get_today_event_id()
        gid = group_id.upper()

        deleted = results_collection.delete_many({
            "event_id": event_id,
            "group_id": gid,
            "match_number": match_number
        })

        embed = make_embed(
            "🔄 Results Reset",
            f"{Theme.SEP}\n\nCleared **{deleted.deleted_count}** team results from Group **{gid}** Match **{match_number}**.\n\n{Theme.SEP}",
            Theme.ORANGE
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="resetallresults", description="[Admin] Wipe all match results for today's event")
    @app_commands.checks.has_permissions(administrator=True)
    async def reset_all_results(self, interaction: discord.Interaction):
        event_id = get_today_event_id()

        deleted = results_collection.delete_many({"event_id": event_id})

        embed = make_embed(
            "🔄 All Results Reset",
            f"{Theme.SEP}\n\nCleared **{deleted.deleted_count}** results for today's event `{event_id}`.\n"
            f"Leaderboard has been reset to zero.\n\n{Theme.SEP}",
            Theme.ORANGE
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ─────────────── POST STANDINGS ───────────────

    @app_commands.command(name="postleaderboard", description="[Admin] Publish the standings to a specific channel")
    @app_commands.describe(channel="Target channel to post the leaderboard")
    @app_commands.checks.has_permissions(administrator=True)
    async def post_leaderboard(self, interaction: discord.Interaction, channel: discord.TextChannel):
        event_id = get_today_event_id()

        results = list(results_collection.find({"event_id": event_id}))
        if not results:
            await interaction.response.send_message(
                embed=error_embed("❌ No Results", "No match results recorded yet today."),
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
        for rank, t in enumerate(sorted_teams[:20], 1):
            medal = get_rank_emoji(rank)
            name = t["team_name"]
            if len(name) > 18:
                name = name[:16] + ".."
            lines.append(f"{medal} **{name}** — `{t['total_points']}` pts  •  💀 `{t['total_kills']}` kills")

        podium = ""
        if len(sorted_teams) >= 3:
            podium = (
                f"\n🥇 **{sorted_teams[0]['team_name']}** — `{sorted_teams[0]['total_points']}` pts\n"
                f"🥈 **{sorted_teams[1]['team_name']}** — `{sorted_teams[1]['total_points']}` pts\n"
                f"🥉 **{sorted_teams[2]['team_name']}** — `{sorted_teams[2]['total_points']}` pts\n"
            )

        embed = make_embed(
            "🏆 Tournament Leaderboard",
            f"{Theme.SEP}\n{podium}\n{Theme.THIN_SEP}\n\n" + "\n".join(lines) + f"\n\n{Theme.SEP}",
            Theme.GOLD,
            f"🏆 Overall Tournament Standings"
        )
        await channel.send(embed=embed)
        await interaction.response.send_message(
            embed=success_embed("✅ Leaderboard Posted", f"Standings posted to {channel.mention}"),
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(PointsCog(bot))
