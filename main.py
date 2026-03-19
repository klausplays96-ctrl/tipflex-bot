import discord
from discord.ext import commands
import os

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# =========================
# ENV VARIABLES (Railway)
# =========================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

TIPFLEX_CHANNEL_ID = int(os.getenv("TIPFLEX_CHANNEL_ID", "0"))
BOT_MGMT_CHANNEL_ID = int(os.getenv("BOT_MGMT_CHANNEL_ID", "0"))
SHIFTS_CHANNEL_ID = int(os.getenv("SHIFTS_CHANNEL_ID", "0"))

TIPFLEX_CHANNEL = os.getenv("TIPFLEX_CHANNEL", "tipflex")
BOT_MGMT_CHANNEL = os.getenv("BOT_MGMT_CHANNEL", "bot-management")
SHIFTS_CHANNEL = os.getenv("SHIFTS_CHANNEL", "shifts")

# =========================
# DEBUG ON START
# =========================
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    print(f"TIPFLEX_CHANNEL_ID={TIPFLEX_CHANNEL_ID}")
    print(f"BOT_MGMT_CHANNEL_ID={BOT_MGMT_CHANNEL_ID}")
    print(f"SHIFTS_CHANNEL_ID={SHIFTS_CHANNEL_ID}")

# =========================
# GLOBAL CHANNEL CHECK (FIXED)
# =========================
@bot.check
async def global_command_check(ctx: commands.Context):
    if ctx.guild is None:
        return False

    # PRIORITY: use IDs
    allowed_ids = set()
    if BOT_MGMT_CHANNEL_ID:
        allowed_ids.add(BOT_MGMT_CHANNEL_ID)
    if SHIFTS_CHANNEL_ID:
        allowed_ids.add(SHIFTS_CHANNEL_ID)

    if allowed_ids:
        if ctx.channel.id in allowed_ids:
            return True

        print(
            f"BLOCKED command={ctx.command} "
            f"channel_id={ctx.channel.id} "
            f"name={ctx.channel.name} "
            f"expected_ids={allowed_ids}"
        )
        await ctx.send("❌ Commands only work in bot-management or shifts.")
        return False

    # FALLBACK: names
    allowed_names = {
        BOT_MGMT_CHANNEL.lower(),
        SHIFTS_CHANNEL.lower()
    }

    if ctx.channel.name.lower() in allowed_names:
        return True

    print(
        f"BLOCKED (name fallback) channel={ctx.channel.name} "
        f"expected={allowed_names}"
    )
    await ctx.send("❌ Commands only work in bot-management or shifts.")
    return False


# =========================
# TIPFLEX CHECK (FIXED)
# =========================
def is_tipflex_channel(message: discord.Message) -> bool:
    if not message.guild:
        return False

    if TIPFLEX_CHANNEL_ID:
        return message.channel.id == TIPFLEX_CHANNEL_ID

    return message.channel.name.lower() == TIPFLEX_CHANNEL.lower()


# =========================
# TEST COMMANDS
# =========================
@bot.command()
async def total(ctx):
    await ctx.send("✅ TOTAL WORKS")

@bot.command()
async def removemoney(ctx):
    await ctx.send("✅ REMOVE WORKS")

@bot.command()
async def shift(ctx):
    await ctx.send("✅ SHIFT WORKS")


# =========================
# RUN BOT
# =========================
bot.run(DISCORD_TOKEN)
