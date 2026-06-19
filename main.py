"""
Mack Bot Tortuga — Main Entry Point
Bot class, event handlers, cog loading, and startup.
"""

import discord
from discord.ext import commands
from config import TOKEN, GUILD_ID, BOT_PREFIX
from database import create_indexes
import keep_alive


# ═══════════════════ BOT CLASS ═══════════════════

class TortugaBot(commands.Bot):
    """Main bot class for Mack Bot Tortuga."""

    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(
            command_prefix=BOT_PREFIX,
            intents=intents,
            help_command=None  # We use our own /help
        )

    async def setup_hook(self):
        """Load all cogs and sync slash commands."""
        print("📦 Loading cogs...", flush=True)

        cog_list = [
            "cogs.registration",
            "cogs.provisioning",
            "cogs.reminders",
            "cogs.admin_panel",
            "cogs.announcements",
            "cogs.help",
            "cogs.points",
        ]

        for cog in cog_list:
            try:
                await self.load_extension(cog)
                print(f"  ✅ Loaded {cog}", flush=True)
            except Exception as e:
                print(f"  ❌ Failed to load {cog}: {e}", flush=True)

        # Sync slash commands
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            print(f"🔄 Synced slash commands to guild {GUILD_ID}", flush=True)
        else:
            await self.tree.sync()
            print("🔄 Synced slash commands globally", flush=True)


# ═══════════════════ EVENTS ═══════════════════

bot = TortugaBot()


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})", flush=True)
    print(f"📡 Connected to {len(bot.guilds)} guild(s)", flush=True)
    print(f"🚀 Mack Bot Tortuga is ready!", flush=True)


@bot.event
async def on_command_error(ctx, error):
    """Global error handler for prefix commands."""
    from utils.embeds import make_embed, error_embed
    from config import Theme

    if isinstance(error, commands.MissingPermissions):
        e = make_embed("🔒 Access Denied", "You don't have permission to use this command.", Theme.ERROR)
        await ctx.send(embed=e, delete_after=10)
    elif isinstance(error, commands.MissingRequiredArgument):
        e = make_embed("⚠️ Missing Argument", f"Required: `{error.param.name}`\nUse `/help` for syntax.", Theme.WARNING)
        await ctx.send(embed=e, delete_after=10)
    elif isinstance(error, commands.CommandNotFound):
        pass  # Silently ignore unknown prefix commands
    elif isinstance(error, commands.CheckFailure):
        pass
    else:
        e = make_embed("❌ Error", f"`{str(error)[:200]}`", Theme.ERROR)
        await ctx.send(embed=e, delete_after=15)
        print(f"[ERROR] {error}", flush=True)


# ═══════════════════ STARTUP ═══════════════════

if __name__ == "__main__":
    # Validate token
    if not TOKEN:
        print("❌ FATAL: TOKEN environment variable is not set!", flush=True)
        exit(1)

    print("✅ TOKEN found", flush=True)

    # Create database indexes
    create_indexes()

    # Start keep-alive web server
    print("🌐 Starting web server...", flush=True)
    keep_alive.keep_alive()

    # Connect to Discord
    print("🚀 Connecting to Discord...", flush=True)
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"❌ FATAL: Bot crashed: {e}", flush=True)
        exit(1)
