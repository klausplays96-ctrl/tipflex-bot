import os
import re
import json
import datetime as dt
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
TIPFLEX_CHANNEL = os.getenv("TIPFLEX_CHANNEL", "tipflex")
BOT_MGMT_CHANNEL = os.getenv("BOT_MGMT_CHANNEL", "bot-management")
SHIFTS_CHANNEL = os.getenv("SHIFTS_CHANNEL", "🕞∥shifts")

SALES_FILE = "sales.json"
SCOREBOARD_FILE = "scoreboard.json"
SHIFT_STATE_FILE = "shift_state.json"

UK_TZ = ZoneInfo("Europe/London")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN missing in .env file.")

AMOUNT_RE = re.compile(r"(?P<cur>[$€£])?\s*(?P<num>\d+(?:[.,]\d{1,2})?)")


# -----------------------
# File helpers
# -----------------------
def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_sales():
    return load_json(SALES_FILE, [])


def save_sales(rows):
    save_json(SALES_FILE, rows)


def load_scoreboard_cfg():
    return load_json(SCOREBOARD_FILE, {})


def save_scoreboard_cfg(cfg):
    save_json(SCOREBOARD_FILE, cfg)


def load_shift_state():
    return load_json(SHIFT_STATE_FILE, {})


def save_shift_state(data):
    save_json(SHIFT_STATE_FILE, data)


# -----------------------
# Time / parsing helpers
# -----------------------
def parse_amount(text: str):
    if not text:
        return None, None
    m = AMOUNT_RE.search(text)
    if not m:
        return None, None
    cur = m.group("cur") or "$"
    num = m.group("num").replace(",", ".")
    try:
        return cur, float(num)
    except ValueError:
        return None, None


def now_utc_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_iso_utc(iso_ts: str):
    try:
        return datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def is_current_week(iso_ts: str) -> bool:
    dt_obj = parse_iso_utc(iso_ts)
    if not dt_obj:
        return False
    now = datetime.now(timezone.utc)
    return dt_obj.isocalendar()[:2] == now.isocalendar()[:2]


# -----------------------
# Money helpers
# -----------------------
def sum_by_currency(rows):
    totals = {}
    for r in rows:
        c = r.get("currency", "$")
        totals[c] = totals.get(c, 0.0) + float(r.get("amount", 0))
    return totals


def calc_net_totals(gross_totals):
    # Net = 80% after OF takes 20%
    return {cur: amt * 0.80 for cur, amt in gross_totals.items()}


def format_totals(totals: dict) -> str:
    if not totals:
        return "0"
    order = ["$", "€", "£"]
    items = []
    for cur in order:
        if cur in totals:
            items.append((cur, totals[cur]))
    for cur, val in totals.items():
        if cur not in order:
            items.append((cur, val))
    return " | ".join([f"{c}{v:.2f}" for c, v in items])


def score_from_totals(totals: dict) -> float:
    if "$" in totals:
        return totals["$"]
    return sum(totals.values())


# -----------------------
# Shift helpers (UK time)
# Shifts:
# 6 PM -> 2 AM
# 2 AM -> 10 AM
# 10 AM -> 6 PM
# -----------------------
def get_shift_info_for_time(now_uk: dt.datetime):
    hour = now_uk.hour

    if 18 <= hour <= 23:
        start_local = now_uk.replace(hour=18, minute=0, second=0, microsecond=0)
        end_local = (start_local + dt.timedelta(days=1)).replace(hour=2, minute=0, second=0, microsecond=0)
        label = "6PM – 2AM"
    elif 0 <= hour < 2:
        prev_day = now_uk - dt.timedelta(days=1)
        start_local = prev_day.replace(hour=18, minute=0, second=0, microsecond=0)
        end_local = now_uk.replace(hour=2, minute=0, second=0, microsecond=0)
        label = "6PM – 2AM"
    elif 2 <= hour < 10:
        start_local = now_uk.replace(hour=2, minute=0, second=0, microsecond=0)
        end_local = now_uk.replace(hour=10, minute=0, second=0, microsecond=0)
        label = "2AM – 10AM"
    else:
        start_local = now_uk.replace(hour=10, minute=0, second=0, microsecond=0)
        end_local = now_uk.replace(hour=18, minute=0, second=0, microsecond=0)
        label = "10AM – 6PM"

    return {
        "label": label,
        "start_local": start_local,
        "end_local": end_local,
        "start_utc": start_local.astimezone(timezone.utc),
        "end_utc": end_local.astimezone(timezone.utc),
    }


def get_current_shift_info():
    now_uk = dt.datetime.now(UK_TZ)
    return get_shift_info_for_time(now_uk)


def get_previous_shift_info(now_uk: dt.datetime):
    current = get_shift_info_for_time(now_uk)
    prev_time = current["start_local"] - dt.timedelta(minutes=1)
    return get_shift_info_for_time(prev_time)


def filter_sales_between(rows, start_utc: datetime, end_utc: datetime):
    filtered = []
    for r in rows:
        ts = parse_iso_utc(r.get("timestamp_utc", ""))
        if ts and start_utc <= ts < end_utc:
            filtered.append(r)
    return filtered


def build_shift_report_text(shift_info, rows):
    title = f"📊 **Shift Report ({shift_info['label']})**"
    if not rows:
        return title + "\n\nNo sales in this shift."

    per_user = {}
    for r in rows:
        uid = r["user_id"]
        if uid not in per_user:
            per_user[uid] = {
                "username": r.get("username", uid),
                "gross_totals": {}
            }
        cur = r.get("currency", "$")
        per_user[uid]["gross_totals"][cur] = per_user[uid]["gross_totals"].get(cur, 0.0) + float(r.get("amount", 0))

    ranked = sorted(per_user.values(), key=lambda x: score_from_totals(x["gross_totals"]), reverse=True)

    lines = [title, ""]
    for entry in ranked:
        gross = entry["gross_totals"]
        net = calc_net_totals(gross)
        lines.append(f"**{entry['username']}** — Gross {format_totals(gross)} | Net {format_totals(net)}")

    lines.append("")
    lines.append(f"Top chatter: **{ranked[0]['username']}** 🏆")
    return "\n".join(lines)


# -----------------------
# Leaderboard helpers
# -----------------------
def build_leaderboard(rows, title, limit=10):
    per_user = {}
    for r in rows:
        uid = r["user_id"]
        if uid not in per_user:
            per_user[uid] = {"username": r.get("username", uid), "totals": {}}
        cur = r.get("currency", "$")
        per_user[uid]["totals"][cur] = per_user[uid]["totals"].get(cur, 0.0) + float(r.get("amount", 0))

    if not per_user:
        return title + "\n(No sales yet.)"

    ranked = sorted(per_user.values(), key=lambda x: score_from_totals(x["totals"]), reverse=True)[:limit]

    lines = []
    for i, entry in enumerate(ranked, start=1):
        lines.append(f"{i}. **{entry['username']}** — {format_totals(entry['totals'])}")

    return title + "\n" + "\n".join(lines)


# -----------------------
# Discord setup
# -----------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


def is_tipflex_channel(message: discord.Message) -> bool:
    return bool(message.guild) and message.channel.name.lower() == TIPFLEX_CHANNEL.lower()


async def update_scoreboard():
    cfg = load_scoreboard_cfg()
    if not cfg.get("channel_id") or not cfg.get("message_id"):
        return

    channel = bot.get_channel(int(cfg["channel_id"]))
    if channel is None:
        return

    try:
        msg = await channel.fetch_message(int(cfg["message_id"]))
    except Exception:
        return

    sales = load_sales()
    weekly = [r for r in sales if is_current_week(r.get("timestamp_utc", ""))]
    all_time = sales

    text = []
    text.append(build_leaderboard(weekly, "🏆 **Leaderboard (This Week)**"))
    text.append("")
    text.append(build_leaderboard(all_time, "📊 **Leaderboard (All-Time)**"))
    text.append("")
    text.append(f"Last update: `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}`")

    await msg.edit(content="\n".join(text))


async def post_shift_report_for_previous_shift():
    now_uk = dt.datetime.now(UK_TZ)
    prev_shift = get_previous_shift_info(now_uk)

    state = load_shift_state()
    shift_key = prev_shift["end_local"].isoformat()

    if state.get("last_posted_shift_end") == shift_key:
        return

    all_sales = load_sales()
    rows = filter_sales_between(all_sales, prev_shift["start_utc"], prev_shift["end_utc"])
    report = build_shift_report_text(prev_shift, rows)

    for guild in bot.guilds:
        channel = discord.utils.get(guild.text_channels, name=SHIFTS_CHANNEL)
        if channel:
            await channel.send(report)
            state["last_posted_shift_end"] = shift_key
            save_shift_state(state)
            return


@tasks.loop(minutes=1)
async def shift_report_loop():
    now_uk = dt.datetime.now(UK_TZ)
    if now_uk.minute == 0 and now_uk.hour in (2, 10, 18):
        await post_shift_report_for_previous_shift()


@shift_report_loop.before_loop
async def before_shift_report_loop():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    print(f"✅ Online as {bot.user} | Watching #{TIPFLEX_CHANNEL} | Commands in #{BOT_MGMT_CHANNEL}")
    print("Loaded commands:", [c.name for c in bot.commands])

    try:
        await update_scoreboard()
    except Exception:
        pass

    if not shift_report_loop.is_running():
        shift_report_loop.start()


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    await bot.process_commands(message)

    if not is_tipflex_channel(message):
        return

    if not message.attachments:
        await message.reply("❌ Tipflex requires a **screenshot attachment** + an **amount** (example: `$25`).")
        return

    cur, amt = parse_amount(message.content)
    if amt is None:
        await message.reply("❌ Please include the **amount** (example: `$25`, `€30.50`, `25`).")
        return

    rows = load_sales()
    rows.append({
        "message_id": str(message.id),
        "user_id": str(message.author.id),
        "username": str(message.author),
        "currency": cur,
        "amount": amt,
        "timestamp_utc": now_utc_iso(),
        "attachment_urls": [a.url for a in message.attachments],
        "content": message.content,
        "entry_type": "sale"
    })
    save_sales(rows)

    await message.add_reaction("✅")
    await update_scoreboard()


# -----------------------
# Global command restriction
# -----------------------
@bot.check
async def global_command_check(ctx: commands.Context):
    allowed_channels = {BOT_MGMT_CHANNEL.lower(), SHIFTS_CHANNEL.lower()}
    if ctx.guild and ctx.channel.name.lower() in allowed_channels:
        return True
    await ctx.send(f"❌ Commands only work in **#{BOT_MGMT_CHANNEL}** or **#{SHIFTS_CHANNEL}**.")
    return False


# -----------------------
# Commands
# -----------------------
@bot.command(name="week")
async def week(ctx: commands.Context, member: discord.Member = None):
    sales = [r for r in load_sales() if is_current_week(r.get("timestamp_utc", ""))]
    if member:
        sales = [r for r in sales if r["user_id"] == str(member.id)]
        await ctx.send(f"🗓️ This week for {member.mention}: {format_totals(sum_by_currency(sales))}")
    else:
        await ctx.send(f"🗓️ This week (all): {format_totals(sum_by_currency(sales))}")


@bot.command(name="total")
async def total(ctx: commands.Context, member: discord.Member = None):
    sales = load_sales()
    if member:
        sales = [r for r in sales if r["user_id"] == str(member.id)]
        await ctx.send(f"📊 All-time for {member.mention}: {format_totals(sum_by_currency(sales))}")
    else:
        await ctx.send(f"📊 All-time (all): {format_totals(sum_by_currency(sales))}")


@bot.command(name="leaderboard")
async def leaderboard(ctx: commands.Context, period: str = "week"):
    sales = load_sales()
    if period.lower() in ("week", "weekly"):
        sales = [r for r in sales if is_current_week(r.get("timestamp_utc", ""))]
        await ctx.send(build_leaderboard(sales, "🏆 **Leaderboard (This Week)**"))
    else:
        await ctx.send(build_leaderboard(sales, "📊 **Leaderboard (All-Time)**"))


@bot.command(name="payroll")
async def payroll(ctx: commands.Context):
    sales = [r for r in load_sales() if is_current_week(r.get("timestamp_utc", ""))]

    per_user = {}
    for r in sales:
        uid = r["user_id"]
        if uid not in per_user:
            per_user[uid] = {"username": r.get("username", uid), "totals": {}}
        cur = r.get("currency", "$")
        per_user[uid]["totals"][cur] = per_user[uid]["totals"].get(cur, 0.0) + float(r.get("amount", 0))

    if not per_user:
        await ctx.send("No weekly sales yet.")
        return

    ranked = sorted(per_user.values(), key=lambda x: score_from_totals(x["totals"]), reverse=True)

    lines = ["💵 **Weekly Payroll (This Week)**"]
    for entry in ranked:
        lines.append(f"- **{entry['username']}** — {format_totals(entry['totals'])}")

    await ctx.send("\n".join(lines))


@bot.command(name="scoreboard")
async def scoreboard(ctx: commands.Context, action: str = "set"):
    if action.lower() != "set":
        await ctx.send("Use: `!scoreboard set`")
        return

    msg = await ctx.send("Setting up scoreboard…")
    cfg = {"channel_id": str(ctx.channel.id), "message_id": str(msg.id)}
    save_scoreboard_cfg(cfg)

    await update_scoreboard()
    await ctx.send("✅ Scoreboard set. It will auto-update after every sale.")


@bot.command(name="resetweek")
@commands.has_permissions(administrator=True)
async def resetweek(ctx: commands.Context):
    await update_scoreboard()
    await ctx.send("✅ Weekly leaderboard reset manually.")


@bot.command(name="resetsales")
@commands.has_permissions(administrator=True)
async def resetsales(ctx: commands.Context, confirm: str = None):
    if confirm != "CONFIRM":
        await ctx.send(
            "⚠️ This will delete **ALL** recorded sales.\n"
            "If you are sure, run: `!resetsales CONFIRM`"
        )
        return

    save_sales([])
    await update_scoreboard()
    await ctx.send("🧹 ✅ All sales have been wiped.")


@bot.command(name="addmoney")
@commands.has_permissions(administrator=True)
async def addmoney(ctx: commands.Context, member: discord.Member, amount: float, currency: str = "$"):
    if amount <= 0:
        await ctx.send("❌ Amount must be more than 0.")
        return

    if currency not in ["$", "€", "£"]:
        await ctx.send("❌ Currency must be one of: $, €, £")
        return

    rows = load_sales()
    rows.append({
        "message_id": f"manual_add_{datetime.now(timezone.utc).timestamp()}",
        "user_id": str(member.id),
        "username": str(member),
        "currency": currency,
        "amount": amount,
        "timestamp_utc": now_utc_iso(),
        "attachment_urls": [],
        "content": f"Manual add by {ctx.author} for {member}",
        "entry_type": "manual_add"
    })
    save_sales(rows)

    await update_scoreboard()
    await ctx.send(f"✅ Added {currency}{amount:.2f} to {member.mention}.")


@bot.command(name="removemoney")
@commands.has_permissions(administrator=True)
async def removemoney(ctx: commands.Context, member: discord.Member, amount: float, currency: str = "$"):
    if amount <= 0:
        await ctx.send("❌ Amount must be more than 0.")
        return

    if currency not in ["$", "€", "£"]:
        await ctx.send("❌ Currency must be one of: $, €, £")
        return

    rows = load_sales()
    rows.append({
        "message_id": f"manual_remove_{datetime.now(timezone.utc).timestamp()}",
        "user_id": str(member.id),
        "username": str(member),
        "currency": currency,
        "amount": -amount,
        "timestamp_utc": now_utc_iso(),
        "attachment_urls": [],
        "content": f"Manual remove by {ctx.author} for {member}",
        "entry_type": "manual_remove"
    })
    save_sales(rows)

    await update_scoreboard()
    await ctx.send(f"✅ Removed {currency}{amount:.2f} from {member.mention}.")


@bot.command(name="history")
@commands.has_permissions(administrator=True)
async def history(ctx: commands.Context, member: discord.Member):
    rows = [r for r in load_sales() if r["user_id"] == str(member.id)]
    if not rows:
        await ctx.send(f"No history found for {member.mention}.")
        return

    last_rows = rows[-10:]
    lines = [f"🧾 Last entries for {member.mention}:"]
    for r in reversed(last_rows):
        entry_type = r.get("entry_type", "sale")
        amount = float(r.get("amount", 0))
        currency = r.get("currency", "$")
        timestamp = r.get("timestamp_utc", "unknown time")
        lines.append(f"- `{entry_type}` | {currency}{amount:.2f} | `{timestamp}`")

    await ctx.send("\n".join(lines))


@bot.command(name="shift")
async def shift(ctx: commands.Context):
    shift_info = get_current_shift_info()
    rows = filter_sales_between(load_sales(), shift_info["start_utc"], shift_info["end_utc"])
    await ctx.send(build_shift_report_text(shift_info, rows))


bot.run(TOKEN)