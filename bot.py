import os
import logging
import json
import asyncio
import requests
from pathlib import Path
from telegram import Update
# --- CHANGE 1: IMPORT JobQueue DIRECTLY ---
from telegram.ext import Application, CommandHandler, ContextTypes, JobQueue

# --- Configuration & Logging (No changes) ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
INDIWTF_TOKEN = os.getenv("INDIWTF_TOKEN")
INDIWTF_API_BASE_URL = "https://indiwtf.com/api"
DATA_FILE = Path("domains.json")
PERIODIC_CHECK_INTERVAL = 30 * 60

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- All helper and command functions remain exactly the same ---
# (I'm trimming them here for brevity, but you should paste the whole file)
def load_data() -> dict:
    if not DATA_FILE.exists(): return {"chat_id": None, "domains": []}
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            data.setdefault("chat_id", None); data.setdefault("domains", [])
            return data
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Error loading data from {DATA_FILE}: {e}")
        return {"chat_id": None, "domains": []}

def save_data(data: dict):
    try:
        with open(DATA_FILE, "w") as f:
            unique_domains = sorted(list(set(data.get("domains", []))))
            data["domains"] = unique_domains
            json.dump(data, f, indent=2)
    except IOError as e: logger.error(f"Error saving data to {DATA_FILE}: {e}")

async def check_domain_status(domain: str) -> dict:
    if not INDIWTF_TOKEN: return {"error": "Indiwtf API token is not configured."}
    url = f"{INDIWTF_API_BASE_URL}/check?domain={domain}&token={INDIWTF_TOKEN}"
    try:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, lambda: requests.get(url, timeout=10))
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"API check failed for {domain}: {e}")
        try: return response.json()
        except: return {"error": str(e)}

def format_status_message(result: dict, domain_to_check: str) -> str:
    if "error" in result: return f"❌ **Error checking `{domain_to_check}`:**\n{result['error']}"
    status = result.get("status", "unknown").upper()
    ip = result.get("ip", "N/A")
    domain = result.get("domain", domain_to_check)
    emoji = "🚫" if status == "BLOCKED" else "✅"
    status_text = "is *BLOCKED*" if status == "BLOCKED" else "is *ALLOWED*"
    return f"{emoji} `{domain}` {status_text} (IP: `{ip}`)"

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = load_data()
    data["chat_id"] = update.effective_chat.id
    save_data(data)
    welcome_text = (
        "👋 **Hello, everyone! The Indiwtf Domain Checker is now active in this group.**\n\n"
        "I will send periodic domain reports to this chat. Any member can manage the watchlist.\n\n"
        "**Commands:**\n"
        "`/add domain1.com domain2.net ...`\n"
        "Adds domains. You can also paste a list of domains on new lines after the command.\n\n"
        "`/remove domain1.com domain2.net ...`\n"
        "Removes one or more domains.\n\n"
        "`/list`\n"
        "Shows all watched domains.\n\n"
        "`/check domain.com`\n"
        "Performs a single, one-time check."
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

def get_domains_from_message(text: str) -> list[str]:
    parts = text.split(maxsplit=1)
    if len(parts) < 2: return []
    return parts[1].split()

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    domains_to_process = get_domains_from_message(update.message.text)
    if not domains_to_process:
        await update.message.reply_text("Usage: `/add domain1.com domain2.com ...`\nYou can also paste a list of domains on new lines after the command.")
        return
    data = load_data()
    current_domains = set(data.get("domains", []))
    domains_to_add = {domain.lower() for domain in domains_to_process}
    newly_added = sorted(list(domains_to_add - current_domains))
    already_exist = sorted(list(domains_to_add & current_domains))
    response_parts = ["**📝 Bulk Add Report**\n"]
    if newly_added:
        data["domains"].extend(newly_added)
        save_data(data)
        response_parts.append(f"✅ Added *{len(newly_added)}* new domains.")
    if already_exist:
        response_parts.append(f"☑️ Skipped *{len(already_exist)}* domains (already on list).")
    await update.message.reply_text("\n".join(response_parts), parse_mode='Markdown')

async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    domains_to_process = get_domains_from_message(update.message.text)
    if not domains_to_process:
        await update.message.reply_text("Usage: `/remove domain1.com domain2.com ...`\nYou can also paste a list of domains on new lines after the command.")
        return
    data = load_data()
    current_domains = set(data.get("domains", []))
    domains_to_remove = {domain.lower() for domain in domains_to_process}
    successfully_removed = sorted(list(domains_to_remove & current_domains))
    not_found = sorted(list(domains_to_remove - current_domains))
    response_parts = ["**🗑️ Bulk Remove Report**\n"]
    if successfully_removed:
        data["domains"] = [d for d in data["domains"] if d not in successfully_removed]
        save_data(data)
        response_parts.append(f"✅ Removed *{len(successfully_removed)}* domains.")
    if not_found:
        response_parts.append(f"❓ Could not remove *{len(not_found)}* domains (not on list).")
    await update.message.reply_text("\n".join(response_parts), parse_mode='Markdown')

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    domains = load_data().get("domains", [])
    if not domains:
        await update.message.reply_text("The watchlist is empty. Use `/add domain.com`.")
        return
    message = "📋 **Current Watchlist:**\n```\n" + "\n".join(f"- {d}" for d in domains) + "\n```"
    await update.message.reply_text(message, parse_mode='Markdown')

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    domains_to_check = get_domains_from_message(update.message.text)
    if not domains_to_check:
        await update.message.reply_text("Usage: `/check domain.com`")
        return
    domain_to_check = domains_to_check[0].lower()
    await update.message.reply_text(f"🔍 Checking `{domain_to_check}`...", parse_mode='Markdown')
    result = await check_domain_status(domain_to_check)
    await update.message.reply_text(format_status_message(result, domain_to_check), parse_mode='Markdown')

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


def main() -> None:
    """Starts the bot."""
    if not TELEGRAM_TOKEN or not INDIWTF_TOKEN:
        logger.critical("Missing TELEGRAM_TOKEN or INDIWTF_TOKEN.")
        return
    
    # --- CHANGE 2: EXPLICITLY CREATE AND PASS THE JobQueue ---
    job_queue = JobQueue()
    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .job_queue(job_queue) # Pass the created job_queue here
        .build()
    )

    # Add Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("add", add_command))
    application.add_handler(CommandHandler("remove", remove_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("check", check_command))
    
    # Schedule the job (this now uses the guaranteed-to-exist job_queue)
    application.job_queue.run_repeating(periodic_check, interval=PERIODIC_CHECK_INTERVAL, first=10)

    logger.info("Bot is starting up...")
    application.run_polling()

if __name__ == "__main__":
    main()
