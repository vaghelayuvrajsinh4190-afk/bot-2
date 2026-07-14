"""
Mack Bot — Main Entry Point
Bot class, event handlers, cog loading, anti-crash system, and startup.
"""

import sys
import signal
import asyncio
import traceback
import discord
from discord.ext import commands
from config import TOKEN, GUILD_ID, BOT_PREFIX
from database import create_indexes
import keep_alive


# ═══════════════════ ANTI-CRASH SYSTEM ═══════════════════

def setup_anti_crash():
    """
    Install global exception handlers so the bot never goes offline
    due to an unhandled exception.
    """

    # Catch unhandled synchronous exceptions
    def global_exception_handler(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        print("=" * 60, flush=True)
        print("🛡️ ANTI-CRASH: Unhandled exception caught!", flush=True)
        print("".join(traceback.format_exception(exc_type, exc_value, exc_traceback)), flush=True)
        print("=" * 60, flush=True)
        print("Bot continues running...", flush=True)

    sys.excepthook = global_exception_handler

    # Catch unhandled async exceptions
    def async_exception_handler(loop, context):
        exception = context.get("exception")
        message = context.get("message", "No message")
        print("=" * 60, flush=True)
        print("🛡️ ANTI-CRASH: Unhandled async exception!", flush=True)
        print(f"Message: {message}", flush=True)
        if exception:
            print(f"Exception: {exception}", flush=True)
            traceback.print_exception(type(exception), exception, exception.__traceback__)
        print("=" * 60, flush=True)
        print("Bot continues running...", flush=True)

    try:
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(async_exception_handler)
    except RuntimeError:
        pass  # No event loop yet, will be set when bot.run() starts


# ═══════════════════ BOT CLASS ═══════════════════

class MackBot(commands.Bot):
    """Main bot class for Mack Bot."""

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

        # Install async exception handler on the running loop
        self.loop.set_exception_handler(self._async_exception_handler)

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
                traceback.print_exc()

        # Sync slash commands dynamically to avoid rate limits
        from database import get_config, set_config
        commands_synced = await asyncio.to_thread(get_config, "commands_synced", False)
        sync_requested = await asyncio.to_thread(get_config, "sync_commands_on_startup", False)

        if not commands_synced or sync_requested:
            if GUILD_ID:
                guild = discord.Object(id=int(GUILD_ID))
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                print(f"🔄 Synced slash commands to guild {GUILD_ID}", flush=True)
            else:
                await self.tree.sync()
                print("🔄 Synced slash commands globally", flush=True)
            await asyncio.to_thread(set_config, "commands_synced", True)
            await asyncio.to_thread(set_config, "sync_commands_on_startup", False)
        else:
            print("🔄 Skipping slash command sync (already synced). Set config 'sync_commands_on_startup' to True to force sync.", flush=True)

    @staticmethod
    def _async_exception_handler(loop, context):
        """Handle unhandled async exceptions within the bot's event loop."""
        exception = context.get("exception")
        message = context.get("message", "No message")
        print("=" * 60, flush=True)
        print("🛡️ ANTI-CRASH: Unhandled async exception in bot loop!", flush=True)
        print(f"Message: {message}", flush=True)
        if exception:
            print(f"Exception: {type(exception).__name__}: {exception}", flush=True)
        print("=" * 60, flush=True)


# ═══════════════════ EVENTS ═══════════════════

bot = MackBot()


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})", flush=True)
    print(f"📡 Connected to {len(bot.guilds)} guild(s)", flush=True)
    print(f"🚀 Mack Bot is ready!", flush=True)
    print(f"🛡️ Anti-crash system active.", flush=True)


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


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    """Global error handler for slash commands."""
    from utils.embeds import error_embed

    if isinstance(error, discord.app_commands.MissingPermissions):
        try:
            await interaction.response.send_message(
                embed=error_embed("🔒 Access Denied", "You don't have permission to use this command."),
                ephemeral=True
            )
        except discord.InteractionResponded:
            pass
    elif isinstance(error, discord.app_commands.CheckFailure):
        try:
            await interaction.response.send_message(
                embed=error_embed("⛔ Check Failed", "You don't meet the requirements for this command."),
                ephemeral=True
            )
        except discord.InteractionResponded:
            pass
    else:
        print(f"[SLASH ERROR] {error}", flush=True)
        traceback.print_exception(type(error), error, error.__traceback__)
        try:
            await interaction.response.send_message(
                embed=error_embed("❌ Error", f"An unexpected error occurred.\n`{str(error)[:200]}`"),
                ephemeral=True
            )
        except discord.InteractionResponded:
            try:
                await interaction.followup.send(
                    embed=error_embed("❌ Error", f"An unexpected error occurred.\n`{str(error)[:200]}`"),
                    ephemeral=True
                )
            except Exception:
                pass


# ═══════════════════ STARTUP ═══════════════════

if __name__ == "__main__":
    # Install anti-crash handlers
    setup_anti_crash()

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

    # Graceful shutdown handler
    def graceful_shutdown(signum, frame):
        print(f"\n🛑 Received signal {signum}, shutting down gracefully...", flush=True)
        sys.exit(0)

    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    # Connect to Discord with auto-reconnect
    print("🚀 Connecting to Discord...", flush=True)
    try:
        bot.run(TOKEN, reconnect=True)
    except KeyboardInterrupt:
        print("🛑 Bot stopped by user.", flush=True)
    except Exception as e:
        print(f"❌ FATAL: Bot crashed: {e}", flush=True)
        traceback.print_exc()
        exit(1)
