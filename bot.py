import os
import logging
import json
import asyncio
import requests
from pathlib import Path
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# --- Configuration ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
INDIWTF_TOKEN = os.getenv("INDIWTF_TOKEN")
INDIWTF_API_BASE_URL = "https://indiwtf.com/api"
DATA_FILE = Path("domains.json")
PERIODIC_CHECK_INTERVAL = 30 * 60  # 30 minutes

# --- Logging Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- Data Persistence Functions ---
def load_data() -> dict:
    if not DATA_FILE.exists():
        return {"chat_id": None, "domains": []}
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            data.setdefault("chat_id", None)
            data.setdefault("domains", [])
            return data
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Error loading data from {DATA_FILE}: {e}")
        return {"chat_id": None, "domains": []}

def save_data(data: dict):
    try:
        with open(DATA_FILE, "w") as f:
            data["domains"] = sorted(list(set(data.get("domains", []))))
            json.dump(data, f, indent=2)
    except IOError as e:
        logger.error(f"Error saving data to {DATA_FILE}: {e}")

# --- Indiwtf API Function ---
async def check_domain_status(domain: str) -> dict:
    if not INDIWTF_TOKEN:
        return {"error": "Indiwtf API token is not configured."}
    url = f"{INDIWTF_API_BASE_URL}/check?domain={domain}&token={INDIWTF_TOKEN}"
    try:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, lambda: requests.get(url, timeout=10))
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"API check failed for {domain}: {e}")
        try:
            return response.json()
        except:
            return {"error": str(e)}

# --- Formatting Function ---
def format_status_message(result: dict, domain_to_check: str) -> str:
    if "error" in result:
        return f"❌ **Error checking `{domain_to_check}`:**\n{result['error']}"
    status = result.get("status", "unknown").upper()
    ip = result.get("ip", "N/A")
    domain = result.get("domain", domain_to_check)
    emoji = "🚫" if status == "BLOCKED" else "✅"
    status_text = "is *BLOCKED*" if status == "BLOCKED" else "is *ALLOWED*"
    return f"{emoji} `{domain}` {status_text} (IP: `{ip}`)"

# --- Telegram Command Handlers ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = load_data()
    data["chat_id"] = update.effective_chat.id
    save_data(data)
    welcome_text = (
        "👋 **Welcome!** I will now send periodic domain reports to this chat.\n\n"
        "**Commands:**\n"
        "`/add domain.com` - Add a domain to the watchlist.\n"
        "`/remove domain.com` - Remove a domain.\n"
        "`/list` - Show all watched domains.\n"
        "`/check domain.com` - Perform a one-time check."
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: `/add domain.com`")
        return
    domain_to_add = context.args[0].lower()
    data = load_data()
    if domain_to_add in data.get("domains", []):
        await update.message.reply_text(f"`{domain_to_add}` is already on the watchlist.")
        return
    data.setdefault("domains", []).append(domain_to_add)
    save_data(data)
    await update.message.reply_text(f"✅ Added `{domain_to_add}` to the watchlist.")

async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: `/remove domain.com`")
        return
    domain_to_remove = context.args[0].lower()
    data = load_data()
    if domain_to_remove not in data.get("domains", []):
        await update.message.reply_text(f"`{domain_to_remove}` is not on the watchlist.")
        return
    data["domains"].remove(domain_to_remove)
    save_data(data)
    await update.message.reply_text(f"🗑️ Removed `{domain_to_remove}` from the watchlist.")

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    domains = load_data().get("domains", [])
    if not domains:
        await update.message.reply_text("The watchlist is empty. Use `/add domain.com` to add one.")
        return
    message = "📋 **Current Watchlist:**\n```\n" + "\n".join(f"- {d}" for d in domains) + "\n```"
    await update.message.reply_text(message, parse_mode='Markdown')

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: `/check domain.com`")
        return
    domain_to_check = context.args[0].lower()
    await update.message.reply_text(f"🔍 Checking `{domain_to_check}`...", parse_mode='Markdown')
    result = await check_domain_status(domain_to_check)
    await update.message.reply_text(format_status_message(result, domain_to_check), parse_mode='Markdown')

# --- Periodic Job ---
async def periodic_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Running periodic check...")
    data = load_data()
    chat_id, domains = data.get("chat_id"), data.get("domains", [])
    if not chat_id or not domains:
        logger.info("Skipping periodic check: no chat_id or domains configured.")
        return
    report_lines = ["🔔 **Periodic Domain Status Report**\n"]
    for domain in domains:
        result = await check_domain_status(domain)
        report_lines.append(format_status_message(result, domain))
        await asyncio.sleep(1)
    await context.bot.send_message(chat_id=chat_id, text="\n".join(report_lines), parse_mode='Markdown')
    logger.info("Periodic report sent.")

# --- Main Bot Setup ---
def main() -> None:
    if not TELEGRAM_TOKEN or not INDIWTF_TOKEN:
        logger.critical("Missing TELEGRAM_TOKEN or INDIWTF_TOKEN. Bot cannot start.")
        return
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("add", add_command))
    application.add_handler(CommandHandler("remove", remove_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("check", check_command))
    application.job_queue.run_repeating(periodic_check, interval=PERIODIC_CHECK_INTERVAL, first=10)
    logger.info("Bot is starting up...")
    application.run_polling()

if __name__ == "__main__":
    main()
