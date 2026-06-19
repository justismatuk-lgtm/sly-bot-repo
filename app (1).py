import asyncio
import json
import os
import random
import time
from datetime import datetime, timedelta, timezone
import discord
from discord import app_commands
from discord.ext import commands, tasks

intents = discord.Intents.default()
intents.message_content = True  # CRUCIAL: Monitoring active chat words!

bot = commands.Bot(command_prefix="!", intents=intents)

# --- CONFIGURATION ---
TARGET_MEMBER_ID = 185187074435973129  # Sly's User ID
DATA_FILE = "booms.json"
CONFIG_FILE = "config.json"
LISTS_FILE = "lists.json"
TRIGGERS_FILE = "salmon_triggers.json"  
QUOTES_FILE = "sly_quotes.json"        
SHOP_FILE = "shop_titles.json"         # NEW: Title database file
# ---------------------

def load_config():
    """Loads the main bot token and global variables from config.json."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading config file: {e}")
            return {}
    else:
        # Create a template if it doesn't exist so it stops crashing
        default_config = {"token": "M"}
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(default_config, f, indent=4)
        except Exception:
            pass
        return default_config

def save_config(config_data):
    """Saves structural changes back to config.json."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4)
    except Exception as e:
        print(f"Error saving config file: {e}")

LAST_USED_TIME = 0  
ACTIVE_BOUNTY_CHANNEL = None  
WEEKLY_TRIGGER_COUNTS = {}

# --- STATIC ASSETS ENGINE ---
def load_static_lists():
    if not os.path.exists(LISTS_FILE):
        print(f"CRITICAL WARNING: {LISTS_FILE} not found! Initializing empty fallback pools.")
        return {"intros": [], "bot_rants": [], "loot_items": []}
    try:
        with open(LISTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error reading asset file {LISTS_FILE}: {e}")
        return {"intros": [], "bot_rants": [], "loot_items": []}

ASSETS = load_static_lists()
INTROS = ASSETS["intros"]
BOT_RANTS = ASSETS["bot_rants"]
LOOT_ITEMS = ASSETS["loot_items"]

# --- DATA STORAGE HELPER FUNCTIONS ---
def load_shop_titles():
    """Loads titles inventory from shop_titles.json."""
    if os.path.exists(SHOP_FILE):
        try:
            with open(SHOP_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("titles", {})
        except Exception as e:
            print(f"Error loading shop file: {e}")
            return {}
    return {}

def load_salmon_triggers():
    default_triggers = [
        "goon", "rage bait", "fuck", "kill", "walmart", "burger king", "whopper", 
        "quarter pounder", "lawn", "mom", "gram", "waifu", "door dash", "rivals",
        "uninstall", "target", "call in", "calling in", "mcdonalds", "fucking", 
        "dump", "salmon", "bk"
    ]
    if not os.path.exists(TRIGGERS_FILE):
        try:
            with open(TRIGGERS_FILE, "w", encoding="utf-8") as f:
                json.dump({"triggers": default_triggers}, f, indent=4)
            return default_triggers
        except Exception:
            return default_triggers
    try:
        with open(TRIGGERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("triggers", default_triggers)
    except Exception:
        return default_triggers

def load_sly_quotes():
    if os.path.exists(QUOTES_FILE):
        try:
            with open(QUOTES_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("quotes", [])
        except Exception:
            return []
    return []

def load_boom_data():
    """Loads user balances and upgrades old profiles to support unlocked/active titles."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
            
            upgraded = False
            for user_id, profile in data.items():
                if "weekly_booms" not in profile:
                    profile["weekly_booms"] = 0
                    upgraded = True
                if "weekly_bounties_caught" not in profile:
                    profile["weekly_bounties_caught"] = 0
                    upgraded = True
                if "weekly_rolls_count" not in profile:
                    profile["weekly_rolls_count"] = 0
                    upgraded = True
                if "career_booms_earned" not in profile:
                    profile["career_booms_earned"] = profile.get("total_booms", 0)
                    upgraded = True
                if "salmon_triggers_tripped" not in profile:
                    profile["salmon_triggers_tripped"] = 0
                    upgraded = True
                # TITLE INVENTORY SCHEMAS HOOK
                if "unlocked_titles" not in profile:
                    profile["unlocked_titles"] = []
                    upgraded = True
                if "active_title" not in profile:
                    profile["active_title"] = None
                    upgraded = True

            if upgraded:
                save_boom_data(data)
            return data
        except Exception:
            return {}
    return {}

def save_boom_data(data):
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Error saving boom data: {e}")

@bot.event
async def on_ready():
    print(f"Logged in successfully as {bot.user.name}")
    load_salmon_triggers()
    load_shop_titles()
    try:
        await bot.tree.sync()
        print("Global command tree sync complete!")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    
    if not weekly_reset_loop.is_running():
        weekly_reset_loop.start()
    if not trigger_report_loop.is_running():
        trigger_report_loop.start()

# ==========================================
# PASSIVE ENGINE: HIGH-STAKES SLY BACKGROUND MONITORING
# ==========================================
BOUNTY_COOLDOWN_UNTIL = 0  # Timestamp when the bot can drop another bounty
ACTIVE_BOUNTY_CHANNEL = None
ACTIVE_BOUNTY_TASK = None  # Holds our 60-second escape timer task

@bot.event
async def on_message(message: discord.Message):
    global ACTIVE_BOUNTY_CHANNEL, BOUNTY_COOLDOWN_UNTIL, ACTIVE_BOUNTY_TASK
    
    if message.author.id == bot.user.id:
        return

    current_time = time.time()

    # 1. CHECK FOR SLY TRIGGER PHRASE
    if message.author.id == TARGET_MEMBER_ID and ACTIVE_BOUNTY_CHANNEL is None:
        if current_time >= BOUNTY_COOLDOWN_UNTIL:
            content_clean = message.content.lower().strip()
            triggers = [
                "goon", "rage bait", "fuck", "kill", "walmart", "burger king", "whopper", 
                "quarter pounder", "lawn", "mom", "gram", "waifu", "door dash", "rivals",
                "uninstall", "target", "call in", "calling in", "mcdonalds", "fucking", 
                "dump", "salmon", "bk"
            ]
            
            if any(trigger in content_clean for trigger in triggers):
                # Lock the engine to this specific channel for the hunt
                ACTIVE_BOUNTY_CHANNEL = message.channel.id
                
                # Find the dedicated #boom-channel globally
                alert_channel = discord.utils.get(message.guild.text_channels, name="boom-channel")
                
                if alert_channel:
                    alert_msg = (
                        f"🚨 **[THE SALMON BOUNTY HAS DROPPED]** 🚨\n"
                        f"Sly just emitted a known trigger phrase somewhere in the server!\n\n"
                        f"🕵️‍♂️ **THE MISSION:** Find the channel where he spoke, and be the first to type `BOOM` in all caps!\n"
                        f"⏰ **TIMER:** You have exactly **60 seconds** before the Salmon escapes and Sly steals the booms!"
                    )
                    await alert_channel.send(alert_msg)
                else:
                    print("❌ Error: Could not find a text channel named '#boom-channel'")

                # Start the background task for the 60-second escape countdown
                async def escape_timer(channel_id):
                    global ACTIVE_BOUNTY_CHANNEL, BOUNTY_COOLDOWN_UNTIL
                    await asyncio.sleep(60)
                    
                    if ACTIVE_BOUNTY_CHANNEL == channel_id:
                        # Nobody caught him! Sly gets the reward
                        stolen_booms = random.randint(1, 5)
                        target_id_str = str(TARGET_MEMBER_ID)
                        
                        boom_data = load_boom_data()
                        if target_id_str not in boom_data:
                            boom_data[target_id_str] = {
                                "name": "Sly Dog", 
                                "total_booms": 0, 
                                "career_booms_earned": 0,
                                "rolls_count": 0, 
                                "roll_timestamps": [],
                                "weekly_booms": 0,
                                "weekly_bounties_caught": 0,
                                "weekly_rolls_count": 0
                            }
                        
                        boom_data[target_id_str]["total_booms"] += stolen_booms
                        boom_data[target_id_str]["career_booms_earned"] += stolen_booms
                        boom_data[target_id_str]["weekly_booms"] += stolen_booms
                        save_boom_data(boom_data)
                        
                        # Reset bounty state
                        ACTIVE_BOUNTY_CHANNEL = None
                        
                        # Apply a random cooldown between 60 and 600 seconds
                        cooldown_seconds = random.randint(60, 600)
                        BOUNTY_COOLDOWN_UNTIL = time.time() + cooldown_seconds
                        
                        # Send failure alert to the #boom-channel
                        if alert_channel:
                            escape_msg = (
                                f"🏃💨 **[THE SALMON HAS ESCAPED!]**\n"
                                f"The server was too slow! Nobody found Sly in time.\n"
                                f"👑 **Sly Dog** has successfully evaded the net and pocketed **+{stolen_booms}** booms for himself!\n"
                                f"🤫 The engine is now dark for the next {int(cooldown_seconds // 60)}m {cooldown_seconds % 60:02.0f}s."
                            )
                            await alert_channel.send(escape_msg)

                # Schedule the task
                ACTIVE_BOUNTY_TASK = bot.loop.create_task(escape_timer(ACTIVE_BOUNTY_CHANNEL))
                return

    # 2. CHECK FOR A CLAIM GUESS
    if ACTIVE_BOUNTY_CHANNEL and message.channel.id == ACTIVE_BOUNTY_CHANNEL:
        if message.content == "BOOM":
            # Cancel the escape timer task immediately since it was caught
            if ACTIVE_BOUNTY_TASK:
                ACTIVE_BOUNTY_TASK.cancel()
                ACTIVE_BOUNTY_TASK = None
                
            ACTIVE_BOUNTY_CHANNEL = None  
            
            user_id = str(message.author.id)
            user_name = message.author.display_name
            bounty_roll = random.randint(1, 5)
            
            boom_data = load_boom_data()
            if user_id not in boom_data:
                boom_data[user_id] = {
                    "name": user_name, 
                    "total_booms": 0, 
                    "career_booms_earned": 0,
                    "rolls_count": 0, 
                    "roll_timestamps": [],
                    "weekly_booms": 0,
                    "weekly_bounties_caught": 0,
                    "weekly_rolls_count": 0
                }
                
            boom_data[user_id]["name"] = user_name
            boom_data[user_id]["total_booms"] += bounty_roll
            boom_data[user_id]["career_booms_earned"] += bounty_roll
            boom_data[user_id]["weekly_booms"] += bounty_roll
            boom_data[user_id]["weekly_bounties_caught"] += 1
            save_boom_data(boom_data)
            
            # Apply the randomized cooldown before the next bounty can drop (60 to 600 seconds)
            cooldown_seconds = random.randint(60, 600)
            BOUNTY_COOLDOWN_UNTIL = time.time() + cooldown_seconds
            
            payout_msg = (
                f"⚡ **🎯 [BOUNTY CLAIMED!] 🎯** ⚡\n"
                f"**{user_name}** successfully tracked down Sly's location and caught the Salmon!\n"
                f"🎁 Awarded **+{bounty_roll}** booms.\n"
                f"💰 Total Wallet Balance: **{boom_data[user_id]['total_booms']}** Booms.\n"
                f"🛰️ Radar cooling down for the next {int(cooldown_seconds // 60)}m {cooldown_seconds % 60:02.0f}s."
            )
            await message.channel.send(payout_msg)
            return

    await bot.process_commands(message)
    
# ==========================================
# COMMAND 1: THE ORIGINAL /WWSD COMMAND (COSTS 5 BOOMS!)
# ==========================================
@bot.tree.command(name="wwsd", description="Costs 5 booms! Pull a random classic quote from the Sly Database.")
async def wwsd(interaction: discord.Interaction):
    global LAST_USED_TIME
    user_id = str(interaction.user.id)
    user_name = interaction.user.display_name
    
    boom_data = load_boom_data()
    
    # 1. Wallet Check: Ensure payment can be completed
    if user_id not in boom_data or boom_data[user_id]["total_booms"] < 5:
        current_balance = boom_data[user_id]["total_booms"] if user_id in boom_data else 0
        await interaction.response.send_message(
            f"❌ **ACCESS DENIED.** The Salmon demands tribute!\n"
            f"Running `/wwsd` costs exactly **5 booms**, but you are currently sitting on a wallet balance of **{current_balance}** booms.\n"
            f"Go run `/wwsd-rollforboom` or type `BOOM` when Sly rages to stack your cash!"
        )
        return

    # 2. Anti-Spam Safeguard: 30% chance to trigger exhaustion filter if spammed within 60 seconds
    current_time = time.time()
    if (current_time - LAST_USED_TIME) < 60:
        if random.random() < 0.30:
            rant = random.choice(BOT_RANTS)
            await interaction.response.send_message(f"🤖 **[Sly Bot Exhaustion System]:** {rant}")
            return

    LAST_USED_TIME = current_time
    await interaction.response.defer()

    # Deduct entry fee upfront
    boom_data[user_id]["total_booms"] -= 5  # Deduct from spending wallet only
    save_boom_data(boom_data)
    remaining_balance = boom_data[user_id]["total_booms"]

    # 3. Secret Loot Drop Mechanic: 3% chance to swipe items off the desk
    if random.random() < 0.03:
        item = random.choice(LOOT_ITEMS)
        loot_response = (
            f"🎒 **[SEARCH FAILURE - LOOT ACQUIRED]**\n"
            f"My search claw missed his text logs, but I managed to swipe this off his desk:\n\n"
            f"📦 **Item:** `{item['name']}`\n"
            f"✨ **Rarity:** {item['rarity']}\n"
            f"🔮 **Effect:** *{item['effect']}*\n\n"
            f"💸 *[Transaction: -5 Booms deducted. Remaining Wallet Balance: {remaining_balance} Booms]*"
        )
        await interaction.followup.send(loot_response)
        return

    # 4. Standard Quote Pull: Loads instantly from sly_quotes.json
    sly_quotes = load_sly_quotes()
    
    if sly_quotes:
        chosen_quote = random.choice(sly_quotes)
        random_intro = random.choice(INTROS).format(name="Sly Dog")

        response = (
            f"**{random_intro}**\n"
            f'"{chosen_quote}"\n\n'
            f"💸 *[Transaction: -5 Booms deducted. Remaining Wallet Balance: {remaining_balance} Booms]*"
        )
        
        # 5. Cashback Extravaganza Roll (10% Chance)
        if random.random() < 0.10:
            boom_data[user_id]["total_booms"] += 5
            save_boom_data(boom_data)
            response += (
                "\n\n💥 **CASHBACK EXTRAVAGANZA!!** Your quote was so explosive, the Salmon King "
                "refunded your 5 entry booms right back to your account! BOOM, BOOM, BOOM, BOOM, BOOM!!!"
            )
        await interaction.followup.send(response)
    else:
        # Emergency rollback if the JSON file is empty so user didn't lose their currency for nothing
        boom_data[user_id]["total_booms"] += 5
        save_boom_data(boom_data)
        await interaction.followup.send("⚠️ The custom `sly_quotes.json` file is empty or missing data! Your 5 Booms have been automatically returned.")

# ==========================================
# COMMAND 2: ALL-TIME BOOM COUNTER (WITH ROLLING 24H LIMIT)
# ==========================================
@bot.tree.command(name="wwsd-rollforboom", description="Roll 1-5 booms! Max 20 uses per 24-hour window.")
async def rollforboom(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    user_name = interaction.user.display_name
    current_time = time.time()
    
    boom_data = load_boom_data()
    
    if user_id not in boom_data:
        boom_data[user_id] = {
            "name": user_name, 
            "total_booms": 0, 
            "career_booms_earned": 0,
            "rolls_count": 0, 
            "roll_timestamps": [],
            "weekly_booms": 0,
            "weekly_bounties_caught": 0,
            "weekly_rolls_count": 0
        }
    
    if "roll_timestamps" not in boom_data[user_id]:
        boom_data[user_id]["roll_timestamps"] = []

    rolling_window_limit = 86400
    valid_rolls = [ts for ts in boom_data[user_id]["roll_timestamps"] if (current_time - ts) < rolling_window_limit]
    boom_data[user_id]["roll_timestamps"] = valid_rolls

    if len(valid_rolls) >= 20:
        oldest_roll = valid_rolls[0]
        time_left = rolling_window_limit - (current_time - oldest_roll)
        
        hours = int(time_left // 3600)
        minutes = int((time_left % 3600) // 60)
        seconds = int(time_left % 60)
        
        await interaction.response.send_message(
            f"🛑 **DAILY LIMIT REACHED!** 🛑\n"
            f"Sorry **{user_name}**, you have already rolled **20 times** in the last 24 hours.\n"
            f"⏳ Your slots are locked. Next charge becomes available in **{hours}h {minutes}m {seconds}s**.\n"
            f"💡 *Tip: You can still gain free booms by racing to claim Sly's chat bounties!*"
        )
        return

    current_roll = random.randint(1, 5)
    boom_data[user_id]["name"] = user_name  
    boom_data[user_id]["total_booms"] += current_roll
    boom_data[user_id]["career_booms_earned"] += current_roll  # Permanent tracker increase
    boom_data[user_id]["weekly_booms"] += current_roll
    boom_data[user_id]["rolls_count"] += 1
    boom_data[user_id]["weekly_rolls_count"] += 1
    boom_data[user_id]["roll_timestamps"].append(current_time)  
    save_boom_data(boom_data)
    
    rolls_remaining = 20 - len(boom_data[user_id]["roll_timestamps"])
    booms_string = ", ".join(["BOOM"] * current_roll)
    
    response = (
        f"🎲 **{user_name}** rolled and scored a **{current_roll}/5**!\n\n"
        f"💥 **THE PAYLOAD:** {booms_string}!!!\n\n"
        f"--- 📊 **ALL-TIME DETONATION STATS** ---\n"
        f"💰 Current Spending Wallet: **{boom_data[user_id]['total_booms']}**\n"
        f"🌟 Lifetime Gross Earnings: **{boom_data[user_id]['career_booms_earned']}**\n"
        f"📅 Daily Slots Remaining: **{rolls_remaining}/20**"
    )
    await interaction.response.send_message(response)

# ==========================================
# COMMAND 3: THE HIGH-STAKES BOOM CASINO
# ==========================================
@bot.tree.command(name="wwsd-gambleboom", description="Risk your all-time booms on a 50/50 coin flip. Double or nothing!")
@app_commands.describe(wager="The exact number of lifetime booms you want to risk.")
async def gambleboom(interaction: discord.Interaction, wager: int):
    user_id = str(interaction.user.id)
    user_name = interaction.user.display_name
    
    if wager <= 0:
        await interaction.response.send_message("❌ You have to wager at least **1** boom.")
        return
        
    boom_data = load_boom_data()
    if user_id not in boom_data or boom_data[user_id]["total_booms"] <= 0:
        await interaction.response.send_message("❌ You are bankrupted of explosions. Go roll for some first.")
        return
        
    current_balance = boom_data[user_id]["total_booms"]
    if wager > current_balance:
        await interaction.response.send_message(f"❌ Bet denied. You only own **{current_balance}** booms in your wallet.")
        return

    if random.choice([True, False]):
        boom_data[user_id]["total_booms"] += wager
        boom_data[user_id]["career_booms_earned"] += wager  # Earned extra booms!
        boom_data[user_id]["weekly_booms"] += wager
        save_boom_data(boom_data)
        response = f"🎰 🟩 **[WIN!]** **{user_name}** won the flip! +{wager} Booms! Wallet Balance: **{boom_data[user_id]['total_booms']}**"
    else:
        boom_data[user_id]["total_booms"] -= wager
        # We do NOT subtract from career_booms_earned here!
        boom_data[user_id]["weekly_booms"] -= wager
        save_boom_data(boom_data)
        response = f"🎰 🟥 **[LOSS!]** **{user_name}** lost the flip! -{wager} Booms. Wallet Balance: **{boom_data[user_id]['total_booms']}**"
        
    await interaction.response.send_message(response)

# ==========================================
# NEW FEATURE COMMANDS: THE FLEX TITLE SHOP SYSTEM
# ==========================================
@bot.tree.command(name="wwsd-shop", description="Browse and purchase legendary visual status card titles!")
async def wwsd_shop(interaction: discord.Interaction):
    shop = load_shop_titles()
    if not shop:
        await interaction.response.send_message("❌ Shop inventory is empty or failing to parse.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🛒 The Official WWSD Boom Shop",
        description="Spend your permanent lifetime hoards to permanently unlock flex titles! Use `/wwsd-buy [name]` to purchase.",
        color=discord.Color.gold()
    )
    
    # Categorize items into organized text blocks
    categorized = {}
    for title_name, data in shop.items():
        tier = data["tier"]
        if tier not in categorized:
            categorized[tier] = []
        categorized[tier].append(f"🔹 **{title_name}** | 💰 Cost: `{data['cost']}` Booms\n*{data['desc']}*")

    for tier, items in categorized.items():
        embed.add_field(name=f"👑 {tier}", value="\n\n".join(items), inline=False)
        
    embed.set_footer(text="No refunds. All transactions final.")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="wwsd-buy", description="Purchase a specific title card from the vault.")
@app_commands.describe(title="The exact name of the title card from the shop catalog.")
async def wwsd_buy(interaction: discord.Interaction, title: str):
    shop = load_shop_titles()
    user_id = str(interaction.user.id)
    boom_data = load_boom_data()
    
    # Locate valid casing regardless of input string variations
    matched_title = next((t for t in shop if t.lower() == title.lower()), None)
    
    if not matched_title:
        await interaction.response.send_message("❌ That title card does not exist in the stock inventory. Double-check spelling!", ephemeral=True)
        return
        
    title_cost = shop[matched_title]["cost"]
    
    if user_id not in boom_data or boom_data[user_id]["total_booms"] < title_cost:
        balance = boom_data[user_id]["total_booms"] if user_id in boom_data else 0
        await interaction.response.send_message(f"❌ **INSUFFICIENT FUNDS.** `{matched_title}` costs **{title_cost}** Booms. Your wallet holds: **{balance}**.", ephemeral=True)
        return
        
    if matched_title in boom_data[user_id].get("unlocked_titles", []):
        await interaction.response.send_message(f"🤝 You already own the `{matched_title}` title card! Use `/wwsd-equip` to throw it on.", ephemeral=True)
        return

    # Process Transaction
    boom_data[user_id]["total_booms"] -= title_cost
    boom_data[user_id]["unlocked_titles"].append(matched_title)
    save_boom_data(boom_data)
    
    await interaction.response.send_message(f"🎉 **PURCHASE SUCCESSFUL!** 🎉\nYou spent **{title_cost}** Booms and unlocked the permanent title card: `[{matched_title}]`!\nRun `/wwsd-equip title: {matched_title}` to flash it.")


@bot.tree.command(name="wwsd-equip", description="Equip or unequip titles you currently own.")
@app_commands.describe(title="The title card name to wear, or leave empty/type 'none' to clear.")
async def wwsd_equip(interaction: discord.Interaction, title: str = "none"):
    user_id = str(interaction.user.id)
    boom_data = load_boom_data()
    
    if user_id not in boom_data or not boom_data[user_id].get("unlocked_titles", []):
        await interaction.response.send_message("❌ You haven't unlocked a single title card yet. Go buy something from the `/wwsd-shop`!", ephemeral=True)
        return

    if title.lower() == "none":
        boom_data[user_id]["active_title"] = None
        save_boom_data(boom_data)
        await interaction.response.send_message("⚙️ Active title badge has been removed. You are currently displaying nothing.")
        return

    matched_title = next((t for t in boom_data[user_id]["unlocked_titles"] if t.lower() == title.lower()), None)
    if not matched_title:
        await interaction.response.send_message("❌ You don't own that title card or spelling was mismatched. Run `/wwsd-whereami` to verify ownership.", ephemeral=True)
        return
        
    boom_data[user_id]["active_title"] = matched_title
    save_boom_data(boom_data)
    await interaction.response.send_message(f"✅ **TITLE EQUIPPED!** Your active rank card display badge is now set to: `[{matched_title}]`.")

# ==========================================
# COMMAND 4: PERSONAL RANK RADAR ENGINE
# ==========================================
@bot.tree.command(name="wwsd-whereami", description="Check your current leaderboard rankings across all categories!")
async def where_ami(interaction: discord.Interaction):
    data = load_boom_data()
    user_id = str(interaction.user.id)
    
    if user_id not in data:
        await interaction.response.send_message(
            f"❌ **{interaction.user.display_name}**, you don't have any stats recorded yet! Quit bein' such a pussy!",
            ephemeral=True
        )
        return

    profile = data[user_id]

    # --- TITLE SHOP CODES INTEGRATION ---
    # Fetch their active title card or fall back if none is equipped
    active_badge = f"🏆 `[{profile.get('active_title')}]`" if profile.get("active_title") else "🚫 *No Title Equipped*"
    
    # Format their custom unlocked titles locker list
    unlocked_titles_list = profile.get("unlocked_titles", [])
    if unlocked_titles_list:
        unlocked_display = ", ".join([f"`{t}`" for t in unlocked_titles_list])
    else:
        unlocked_display = "*None (Visit `/wwsd-shop` to buy custom cards!)*"
    # ------------------------------------

    # 1. Calculate Lifetime Booms Rank using the absolute "career_booms_earned" metric
    lifetime_sorted = sorted(data.items(), key=lambda x: x[1].get("career_booms_earned", 0), reverse=True)
    lifetime_rank = next((i + 1 for i, (uid, _) in enumerate(lifetime_sorted) if uid == user_id), "N/A")
    lifetime_total_gross = profile.get("career_booms_earned", 0)
    current_wallet = profile.get("total_booms", 0)

    # 2. Calculate Weekly Booms Rank
    weekly_booms_sorted = sorted(data.items(), key=lambda x: x[1].get("weekly_booms", 0), reverse=True)
    weekly_booms_rank = next((i + 1 for i, (uid, _) in enumerate(weekly_booms_sorted) if uid == user_id), "N/A")
    weekly_booms_total = profile.get("weekly_booms", 0)

    # 3. Calculate Weekly Bounties Caught Rank
    weekly_bounties_sorted = sorted(data.items(), key=lambda x: x[1].get("weekly_bounties_caught", 0), reverse=True)
    weekly_bounties_rank = next((i + 1 for i, (uid, _) in enumerate(weekly_bounties_sorted) if uid == user_id), "N/A")
    weekly_bounties_total = profile.get("weekly_bounties_caught", 0)

    embed = discord.Embed(
        title=f"📊 Personal Standings: {interaction.user.display_name}",
        description=f"**Active Equipped Card:** {active_badge}", # Placed prominently right underneath their name
        color=discord.Color.blue(),
        timestamp=datetime.utcnow()
    )
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    
    embed.add_field(
        name="🏆 Absolute Career Booms", 
        value=f"**Leaderboard Rank:** #{lifetime_rank}\n**Total Ever Earned:** {lifetime_total_gross} Booms\n💼 *Current Spending Wallet:* {current_wallet} Booms", 
        inline=False
    )
    embed.add_field(
        name="📅 Weekly Booms", 
        value=f"**Rank:** #{weekly_booms_rank}\n**Weekly Accumulation:** {weekly_booms_total}", 
        inline=True
    )
    embed.add_field(
        name="🎣 Salmon Hunting", 
        value=f"**Rank:** #{weekly_bounties_rank}\n**Bounties Caught:** {weekly_bounties_total}", 
        inline=True
    )
    
    # Shows everyone what titles they have hiding inside their personal collection
    embed.add_field(
        name="🎒 Unlocked Titles Locker",
        value=unlocked_display,
        inline=False
    )
    
    embed.set_footer(text="Maybe you're number 1, maybe you're not, maybe go fuck yourself!!!")

    await interaction.response.send_message(embed=embed)

# ==========================================
# COMMAND 5: INTERACTIVE HIGH-LOW (THE SALMON RIVER)
# ==========================================
class HighLowView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction, wager: int, initial_card: int, user_id: str):
        super().__init__(timeout=45.0)
        self.orig_interaction = interaction
        self.wager = wager
        self.current_card = initial_card
        self.user_id = user_id
        self.multiplier = 1.0
        self.round_num = 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("❌ This is not your river!", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        try:
            await self.orig_interaction.edit_original_response(
                content=f"⏰ **GAME OVER:** Time expired in the Salmon River! You abandoned your swim and lost your wager of **{self.wager}** booms.",
                view=None
            )
        except Exception:
            pass

    async def process_guess(self, interaction: discord.Interaction, guess: str):
        await interaction.response.defer()
        
        next_card = random.randint(1, 13)
        card_names = {1: "Ace", 11: "Jack", 12: "Queen", 13: "King"}
        def get_card_str(val): return f"🃏 **{card_names.get(val, str(val))}**"

        is_correct = False
        if guess == "higher" and next_card > self.current_card:
            is_correct = True
        elif guess == "lower" and next_card < self.current_card:
            is_correct = True
        elif next_card == self.current_card:
            is_correct = False

        if is_correct:
            multiplier_steps = [1.5, 2.2, 3.5, 5.0, 8.0]
            self.multiplier = multiplier_steps[min(self.round_num - 1, len(multiplier_steps) - 1)]
            potential_payout = int(self.wager * self.multiplier)
            
            old_card_str = get_card_str(self.current_card)
            new_card_str = get_card_str(next_card)
            self.current_card = next_card
            self.round_num += 1

            embed_text = (
                f"🟩 **CORRECT GUESS!**\n\n"
                f"The card flipped from {old_card_str} to {new_card_str}.\n"
                f"📈 Current Multiplier: **{self.multiplier}x**\n"
                f"💰 Potential Cashout: **{potential_payout}** Booms\n\n"
                f"Do you want to press your luck and swim upstream, or take the safe money right now?"
            )
            
            for item in self.children:
                if item.label == "Cash Out":
                    item.disabled = False
                    
            await interaction.edit_original_response(content=embed_text, view=self)
        else:
            boom_data = load_boom_data()
            boom_data[self.user_id]["total_booms"] -= self.wager
            boom_data[self.user_id]["weekly_booms"] -= self.wager
            save_boom_data(boom_data)

            old_card_str = get_card_str(self.current_card)
            new_card_str = get_card_str(next_card)

            loss_text = (
                f"🟥 **💥 THE CURRENT HAS TAKEN YOU!**\n\n"
                f"The card flipped from {old_card_str} to {new_card_str}.\n"
                f"Your guess was wrong. You got caught in the current and lost your entire stake!\n"
                f"💸 **Result:** -{self.wager} Booms. Wallet Balance: **{boom_data[self.user_id]['total_booms']}**"
            )
            self.stop()
            await interaction.edit_original_response(content=loss_text, view=None)

    @discord.ui.button(label="Higher", style=discord.ButtonStyle.green)
    async def higher_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.process_guess(interaction, "higher")

    @discord.ui.button(label="Lower", style=discord.ButtonStyle.danger)
    async def lower_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.process_guess(interaction, "lower")

    @discord.ui.button(label="Cash Out", style=discord.ButtonStyle.blurple, disabled=True)
    async def cash_out_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        
        boom_data = load_boom_data()
        net_profit = int(self.wager * self.multiplier) - self.wager
        boom_data[self.user_id]["total_booms"] += net_profit
        boom_data[self.user_id]["career_booms_earned"] += net_profit  # Add clean profits to lifetime stat tracker
        boom_data[self.user_id]["weekly_booms"] += net_profit
        save_boom_data(boom_data)

        win_text = (
            f"💰 **🎉 CASH OUT SUCCESSFUL! 🎉** 💰\n"
            f"You decided to take the money and run at **{self.multiplier}x**!\n"
            f"🎁 Total Winnings: **{int(self.wager * self.multiplier)}** Booms (Net Profit: +{net_profit}).\n"
            f"📊 New Wallet Balance: **{boom_data[self.user_id]['total_booms']}** Booms."
        )
        self.stop()
        await interaction.edit_original_response(content=win_text, view=None)


@bot.tree.command(name="wwsd-riverboom", description="Swim up the Salmon River! Guess higher/lower on card draws to compound your payout.")
@app_commands.describe(wager="The number of booms you want to stake on the Salmon River.")
async def ladderboom(interaction: discord.Interaction, wager: int):
    user_id = str(interaction.user.id)
    user_name = interaction.user.display_name
    
    if wager <= 0:
        await interaction.response.send_message("❌ You must stake at least **1** boom to enter the river.", ephemeral=True)
        return
        
    boom_data = load_boom_data()
    if user_id not in boom_data or boom_data[user_id]["total_booms"] < wager:
        current_balance = boom_data[user_id]["total_booms"] if user_id in boom_data else 0
        await interaction.response.send_message(
            f"❌ **STAKE DENIED.** You tried to wager **{wager}** booms, but you only have **{current_balance}** in your vault wallet.",
            ephemeral=True
        )
        return

    initial_card = random.randint(2, 12)  
    card_names = {11: "Jack", 12: "Queen"}
    card_str = card_names.get(initial_card, str(initial_card))

    start_text = (
        f"🪜 **WELCOME TO THE SALMON RIVER, {user_name.upper()}!** 🪜\n"
        f"You are risking **{wager}** booms. Each consecutive correct guess skyrockets your payout multiplier.\n"
        f"If you hit a direct numeric tie or guess wrong, you lose it all.\n\n"
        f"▶️ Your starting base card is a: 🃏 **{card_str}**\n\n"
        f"Will the next card drawn be **Higher** or **Lower**?"
    )

    view = HighLowView(interaction, wager, initial_card, user_id)
    await interaction.response.send_message(start_text, view=view)

# ==========================================
#         AUTOMATIC WEEKLY RESET
# ==========================================
@tasks.loop(seconds=60)
async def weekly_reset_loop():
    """Runs a check every 60 seconds. Friday nights at 8:00 PM EST drops summaries."""
    est_offset = timedelta(hours=-5) 
    now = datetime.now(timezone(est_offset))
    
    # Target: Friday at 20:00 (8:00 PM EST)
    if now.weekday() == 4 and now.hour == 20 and now.minute == 0:
        boom_data = load_boom_data()
        if not boom_data:
            return

        # --- BROADCAST PRE-RESET HYPING ANNOUNCEMENT ---
        alert_msg = "🚨 **THE CLOCK STRICKES 8!** 🚨\nDrop your fishing rods, step away from the coin toss, and grab your Dr. Pete!! It's Friday night, the numbers are being counted by the royal guards, and it's time for the **Weekly Boom Roundup**! Let's see who dominated the BOOM... 📉💥"

        for guild in bot.guilds:
            announcement_channel = discord.utils.get(guild.text_channels, name="boom-channel")
            if announcement_channel:
                try:
                    await announcement_channel.send(alert_msg)
                except Exception as e:
                    print(f"Failed to post alert to {guild.name}: {e}")

        profiles = list(boom_data.values())
        
        # 1. Highest Weekly Yield Winner
        top_earner = max(profiles, key=lambda x: x.get("weekly_booms", 0), default=None)
        # 2. Most Bounties Caught Winner
        top_hunter = max(profiles, key=lambda x: x.get("weekly_bounties_caught", 0), default=None)
        # 3. Total Gambling Engagements Winner
        most_active = max(profiles, key=lambda x: x.get("weekly_gambles_count", 0), default=None)

        leaderboard_msg = "🚨 📉 **WEEKLY RESET DISPATCH** 📉 🚨\n\n"
        
        # --- BOOMS LEADERBOARD ---
        if top_earner and top_earner.get("weekly_booms", 0) > 0:
            leaderboard_msg += f"🏆 **THE LORD OF THE BOOMS:** `{top_earner['name']}` has fine collection with a massive yield of **{top_earner['weekly_booms']}** Booms!\n"
        else:
            leaderboard_msg += "🏆 **THE LORD OF THE BOOM:** No production assets were registered this week.\n"

        # --- BOUNTIES LEADERBOARD ---
        if top_hunter and top_hunter.get("weekly_bounties_caught", 0) > 0:
            leaderboard_msg += f"🤠 **THE SALMON HUNTER:** `{top_hunter['name']}` dominated the board, bringing in **{top_hunter['weekly_bounties_caught']}** bounties this week!\n"
        else:
            leaderboard_msg += "🤠 **THE SALMON HUNTER:** No bounties were claimed this week.\n"

        # --- GAMBLING LEADERBOARD ---
        if most_active and most_active.get("weekly_gambles_count", 0) > 0:
            leaderboard_msg += f"🎲 **THE GAMBLING ADDICT:** `{most_active['name']}` has a serious problem, placing **{most_active['weekly_gambles_count']}** total bets across all games!\n\n"
        else:
            leaderboard_msg += "🎲 **THE GAMBLING ADDICT:** No games were played this week.\n\n"

        leaderboard_msg += "🔄 *Toilet flushed. Weekly stats have dropped back to zero. Good hunting next week.*"

        # Broadcast report to targeted channels
        for guild in bot.guilds:
            channel = discord.utils.get(guild.text_channels, name="boom-ticker") or guild.text_channels[0]
            if channel:
                try:
                    await channel.send(leaderboard_msg)
                except Exception as e:
                    print(f"Failed to post to {guild.name}: {e}")

        # Complete cleanup phase
        for uid in boom_data:
            boom_data[uid]["weekly_booms"] = 0
            boom_data[uid]["weekly_bounties_caught"] = 0
            boom_data[uid]["weekly_gambles_count"] = 0
            
        save_boom_data(boom_data)
        print("Weekly metrics clean wipe executed successfully.")
        
        # Avoid double-firing within the same minute frame
        await asyncio.sleep(60)

# ==========================================
#   TRIGGER WORD CHART LOOP (8:30 PM EST)
# ==========================================
@tasks.loop(seconds=60)
async def trigger_report_loop():
    global WEEKLY_TRIGGER_COUNTS
    est_offset = timedelta(hours=-5) 
    now = datetime.now(timezone(est_offset))
    
    if now.weekday() == 4 and now.hour == 20 and now.minute == 30:
        sorted_hits = sorted(WEEKLY_TRIGGER_COUNTS.items(), key=lambda x: x[1], reverse=True)
        active_hits = [(word, count) for word, count in sorted_hits if count > 0]
        report_msg = "🎣 **[THE WEEKLY SALMON TRIGGER BREAKDOWN!]** 🎣\n"
        
        if not active_hits:
            report_msg += "🟩 *Unbelievable. The target did not utter a single recorded trigger keyword this entire week.*"
        else:
            report_msg += "```text\n"
            max_word_len = max(max([len(word) for word, _ in active_hits]), 12)
            for word, count in active_hits:
                report_msg += f"{word.upper()}{' ' * (max_word_len - len(word))} | {'█' * count} ({count})\n"
            report_msg += "```\n"
            
        for guild in bot.guilds:
            alert_channel = discord.utils.get(guild.text_channels, name="boom-channel")
            if alert_channel:
                try: await alert_channel.send(report_msg)
                except Exception: pass
        WEEKLY_TRIGGER_COUNTS = {}
        print("Weekly trigger frequency data flushed clean successfully.")
        
        # Avoid double-firing within the same minute frame
        await asyncio.sleep(60)


# --- RUN THE BOT FROM CONFIG ---
config = load_config()
bot_token = config.get("TOKEN")

if bot_token:
    bot.run(bot_token)
else:
    print("❌ ERROR: Could not find 'TOKEN' inside config.json. Please ensure the file exists and is formatted correctly.")
