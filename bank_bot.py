# bank_bot.py
# Telegram Bank Bot – "River Bank"
# by riv 🩵

import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import html
import os
from dotenv import load_dotenv
import keep_alive

load_dotenv()

# ==================== CONFIG ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Load from env
if not BOT_TOKEN:
    # Fallback for local testing if env var not set, though .env is preferred
    BOT_TOKEN = "8347471576:AAGNPTpW1UsRrVbAPgFGvW7qMobmZg168dM"

OWNER_ID = 1768830793                 # Bank Owner Telegram ID
ADMINS = ["reviosa", "zaonoror"]     # Bank Managers by username
SPREADSHEET_NAME = "RBank"
SERVICE_ACCOUNT_FILE = "tg-project-01-b8db80779692.json"

CURRENCY = "₱"
BANK_NAME = "River Bank"
LOGS_CHANNEL_ID = -1003381183744
# =================================================

logging.basicConfig(level=logging.INFO)

# ==================== GOOGLE SHEETS SETUP ====================
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

google_creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")

if google_creds_json:
    # Load from Environment Variable (Best for Render/Cloud)
    creds_dict = json.loads(google_creds_json)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
else:
    # Load from File (Best for Local)
    creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, scope)

client = gspread.authorize(creds)

try:
    sheet = client.open(SPREADSHEET_NAME).sheet1
except gspread.SpreadsheetNotFound:
    raise Exception(
        f"✖️ Spreadsheet '{SPREADSHEET_NAME}' not found. "
        f"Share it with your service account email and give editor access."
    )
except gspread.exceptions.APIError as e:
    raise Exception(
        f"✖️ Google Sheets API error: {e}. Ensure Sheets & Drive APIs are enabled."
    )

# ==================== HELPERS ====================
def find_user_row(user_id):
    data = sheet.col_values(1)
    for i, val in enumerate(data):
        if str(val) == str(user_id):
            return i + 1
    return None

def format_datetime():
    return datetime.now().strftime("%m-%d-%Y, %I:%M %p")

def format_money(amount):
    """Format number with commas for readability"""
    return f"{CURRENCY}{int(amount):,}"

def can_modify(user):
    return user.id == OWNER_ID or (user.username in ADMINS)

def save_admins():
    with open("admins.json", "w") as f:
        json.dump(ADMINS, f)

async def send_log(message, context):
    try:
        await context.bot.send_message(LOGS_CHANNEL_ID, message, parse_mode="HTML")
    except:
        logging.warning("Failed to send log message.")

def find_user_by_username(username):
    """Find user row by username (with or without @)"""
    username = username.lstrip("@")
    data = sheet.col_values(3)  # Username column
    for i, val in enumerate(data):
        if val.lstrip("@").lower() == username.lower():
            return i + 1
    return None

async def delete_msg_job(context: ContextTypes.DEFAULT_TYPE):
    """Job to delete a message after a delay."""
    job = context.job
    try:
        await context.bot.delete_message(chat_id=job.chat_id, message_id=job.data)
    except Exception as e:
        logging.warning(f"Failed to auto-delete message: {e}")

def schedule_auto_delete(context, chat_id, message_id, delay=120):
    """Schedule message deletion."""
    if context.job_queue:
        context.job_queue.run_once(delete_msg_job, delay, chat_id=chat_id, data=message_id)



# ==================== COMMANDS ====================
async def new(update, context):
    """Create a new account (Admin only)."""
    if not can_modify(update.effective_user):
        await update.message.reply_text("✖️ Only the bank owner or managers can create accounts.")
        return

    # Determine target (Reply or Mention)
    target = None

    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
    else:
        await update.message.reply_text("ℹ️ Reply to a user to create their account.")
        return

    # Check if already exists
    row = find_user_row(target.id)
    if row:
        await update.message.reply_text("✖️ This user already has an account.")
        return

    name = target.full_name if target.full_name else target.first_name
    username = f"@{target.username}" if target.username else ""
    # Store clean name, link generated dynamically
    link = f"<a href='tg://user?id={target.id}'>{html.escape(target.first_name)}</a>"

    sheet.append_row([target.id, name, username, link, "0", format_datetime(), ""])
    
    msg = (
        f"account created for {link} — {target.id} ☑️"
    )
    await update.message.reply_text(msg, parse_mode="HTML")
    await send_log(f"✨ New account created for {name} ({target.id}) by {update.effective_user.first_name}", context)

def get_contribution_breakdown(row):
    """
    Reads Advance Payments from column 8 (stored as JSON).
    Returns a formatted string of contributions.
    """
    try:
        advance_json = sheet.cell(row, 8).value
        if not advance_json:
            return ""
        
        advance = json.loads(advance_json)
        if not advance:
            return ""
        
        result = ""
        sorted_contribs = sorted(advance.items(), key=lambda item: item[1], reverse=True)
        
        for admin, amount in sorted_contribs:
            result += f"☁️ {admin}: {format_money(amount)}\n"
        
        return result
    except (json.JSONDecodeError, Exception):
        return ""

async def add(update, context):
    if not can_modify(update.effective_user):
        await update.message.reply_text("✖️ Only the bank owner or managers can modify balances.")
        return

    # Case 1: Reply to a user
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
        if not context.args:
            await update.message.reply_text("Usage: /add [amount] (replying to a user)")
            return
        amount_arg = context.args[0]
    
    # Case 2: Mention a user (@username)
    elif len(context.args) >= 2 and context.args[0].startswith("@"):
        target_username = context.args[0]
        amount_arg = context.args[1]
        
        target_row = find_user_by_username(target_username)
        if not target_row:
            await update.message.reply_text(f"✖️ User {target_username} not found.")
            return
        
        # Determine name, etc. from sheet since we don't have a User object easily
        target_data = sheet.row_values(target_row)
        # Construct a dummy object or just use ID/Name directly. 
        # For consistency with reply logic, let's fetch ID and Name.
        # But `row` logic is uniform. Let's just get the row.
        # Wait, the rest of the function uses `target.id`. 
        # We need to adapt the logic to work with just ROW, or fetch ID.
        # Let's rewrite to use ROW primarily.
        
        # To avoid complex refactoring of the shared logic below, let's just use the ROW.
        # But we need target ID and Name for logging/reply.
        target_id = target_data[0]
        target_name = target_data[1]
        
        # We need to handle this divergence. 
        # Let's unify: Get ROW first.
        row = target_row
    else:
        await update.message.reply_text("Usage:\n• Reply: /add [amount]\n• Mention: /add @user [amount]")
        return

    # --- Unified Logic ---
    # Convert amount
    try:
        amount = int(amount_arg)
    except ValueError:
        await update.message.reply_text("Invalid amount. Use an integer.")
        return

    # If we didn't get row from Mention case, get it from Reply case
    if update.message.reply_to_message:
        # We already have target and row lookup needed
        target = update.message.reply_to_message.from_user
        row = find_user_row(target.id)
        if not row:
            await update.message.reply_text("No account yet.")
            return
        target_first_name = target.first_name
        target_id = target.id
    else:
        # It was the mention case
        # row was already set
        # We need target_first_name for the message
        target_data = sheet.row_values(row) 
        # CSV Cols: 0=ID, 1=Name, 2=Username...
        target_first_name = target_data[1] # Use full name/name from sheet
        target_id = target_data[0]

    current_balance = int(sheet.cell(row, 5).value)
    new_balance = current_balance + amount

    sheet.update_cell(row, 5, str(new_balance))
    sheet.update_cell(row, 6, format_datetime())
    
    # Include admin name in transaction log
    admin_name = update.effective_user.first_name
    prev_tx = sheet.cell(row, 7).value
    new_tx = f"{format_datetime()} + {CURRENCY}{amount} {admin_name}\n{prev_tx}" if prev_tx else f"{format_datetime()} + {CURRENCY}{amount} {admin_name}"
    sheet.update_cell(row, 7, new_tx)

    # Track who added (column 8) - cumulative contributions
    advance_json = sheet.cell(row, 8).value
    contributions = json.loads(advance_json) if advance_json else {}
    contributions[admin_name] = contributions.get(admin_name, 0) + amount
    sheet.update_cell(row, 8, json.dumps(contributions))

    await update.message.reply_text(
        f"{target_first_name} added {CURRENCY}{amount}.\nNew balance: {CURRENCY}{new_balance}.",
        parse_mode="HTML"
    )
    await send_log(f"� {update.effective_user.first_name} added {CURRENCY}{amount} to {target_first_name} ({target_id})", context)



async def use(update, context):
    if not can_modify(update.effective_user):
        await update.message.reply_text("✖️ Only the bank owner or managers can modify balances.")
        return

    # Case 1: Reply to a user
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
        if not context.args:
            await update.message.reply_text("Usage: /use [amount] (replying to a user)")
            return
        amount_arg = context.args[0]
    
    # Case 2: Mention a user (@username)
    elif len(context.args) >= 2 and context.args[0].startswith("@"):
        target_username = context.args[0]
        amount_arg = context.args[1]
        
        row = find_user_by_username(target_username)
        if not row:
            await update.message.reply_text(f"✖️ User {target_username} not found.")
            return
    else:
        await update.message.reply_text("Usage:\n• Reply: /use [amount]\n• Mention: /use @user [amount]")
        return

    # --- Unified Logic ---
    try:
        amount = int(amount_arg)
    except ValueError:
        await update.message.reply_text("Invalid amount. Use an integer.")
        return

    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
        row = find_user_row(target.id)
        if not row:
            await update.message.reply_text("No account yet.")
            return
        target_first_name = target.first_name
        target_id = target.id
    else:
        target_data = sheet.row_values(row) 
        target_first_name = target_data[1]
        target_id = target_data[0]

    current_balance = int(sheet.cell(row, 5).value)
    new_balance = current_balance - amount

    sheet.update_cell(row, 5, str(new_balance))
    sheet.update_cell(row, 6, format_datetime())
    
    # Include admin name in transaction log
    admin_name = update.effective_user.first_name
    prev_tx = sheet.cell(row, 7).value
    new_tx = f"{format_datetime()} - {CURRENCY}{amount} {admin_name}\n{prev_tx}" if prev_tx else f"{format_datetime()} - {CURRENCY}{amount} {admin_name}"
    sheet.update_cell(row, 7, new_tx)

    # Deduct from contributions (column 8) - realtime tracking
    contrib_json = sheet.cell(row, 8).value
    contributions = json.loads(contrib_json) if contrib_json else {}
    if admin_name in contributions:
        contributions[admin_name] -= amount
        if contributions[admin_name] <= 0:
            del contributions[admin_name]  # Remove if zero or negative
        sheet.update_cell(row, 8, json.dumps(contributions))

    await update.message.reply_text(
        f"{target_first_name} deducted {CURRENCY}{amount}.\nNew balance: {CURRENCY}{new_balance}.",
        parse_mode="HTML"
    )
    await send_log(f"💸 {update.effective_user.first_name} deducted {CURRENCY}{amount} from {target_first_name} ({target_id})", context)


# ==================== BALANCE BY USERNAME ====================

async def prom(update, context):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("✖️ Only the bank owner can promote managers.")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user to promote them.")
        return

    target = update.message.reply_to_message.from_user
    if target.id == OWNER_ID:
        await update.message.reply_text("The owner cannot be modified.")
        return
    if target.username in ADMINS:
        await update.message.reply_text(f"{target.first_name} is already a bank manager.")
        return

    ADMINS.append(target.username)
    save_admins()
    sent_msg = await update.message.reply_text(f"✨ {target.first_name} (@{target.username}) is now a Bank Manager!")
    schedule_auto_delete(context, update.effective_chat.id, sent_msg.message_id)
    await send_log(f"👑 {target.first_name} (@{target.username}) promoted to manager.", context)

async def dem(update, context):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("✖️ Only the bank owner can demote managers.")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user to demote them.")
        return

    target = update.message.reply_to_message.from_user
    if target.id == OWNER_ID:
        await update.message.reply_text("The owner cannot be modified.")
        return
    if target.username not in ADMINS:
        await update.message.reply_text(f"{target.first_name} is not a bank manager.")
        return

    ADMINS.remove(target.username)
    save_admins()
    sent_msg = await update.message.reply_text(f"❎ {target.first_name} (@{target.username}) has been demoted.")
    schedule_auto_delete(context, update.effective_chat.id, sent_msg.message_id)
    await send_log(f"⚠️ {target.first_name} (@{target.username}) demoted from manager.", context)

async def bal(update, context):
    user = update.effective_user
    row = find_user_row(user.id)
    if not row:
        await update.message.reply_text("✖️ No account yet. Use /new to create one!")
        return

    data = sheet.row_values(row)
    name, username, link, balance, last = data[1], data[2], data[3], data[4], data[5]
    
    msg = (
        f"💳 <b>Account Details</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 {name} {username}\n"
        f"🆔 {user.id}\n\n"
        f"🧾 <b>Balance:</b> {format_money(balance)}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 Last updated: {last}"
    )
    keyboard = [
        [InlineKeyboardButton("🧾 Transactions", callback_data=f"tx_{user.id}"),
         InlineKeyboardButton("✖️ Close", callback_data=f"close_{user.id}")]
    ]
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

# ==================== BUTTON CALLBACK ====================
async def button_callback(update, context):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    display_user = query.from_user
    data = query.data

    # Transaction history
    if data.startswith("tx_"):
        target_id_str = data.split("_")[1]
        target_row = find_user_row(int(target_id_str))
        
        if not target_row:
             await query.answer("User not found", show_alert=True)
             return

        transactions = sheet.cell(target_row, 7).value
        balance = sheet.cell(target_row, 5).value
        tx_list = transactions.split("\n") if transactions else []
        
        if not tx_list or tx_list == ['']:
            tx_display = "No transactions yet."
        else:
            tx_display = "\n".join([f"• {t}" for t in tx_list[:10]])
            
        msg = (
            f"🧾 <b>Transaction History</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{tx_display}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"total: {len(tx_list)} transactions\n"
        )
        keyboard = [
            [InlineKeyboardButton("◀️ Back", callback_data=f"back_{target_id_str}"),
             InlineKeyboardButton("✖️ Close", callback_data=f"close_{target_id_str}")]
        ]
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    # Back to account details
    elif data.startswith("back_"):
        target_id_str = data.split("_")[1]
        target_row = find_user_row(int(target_id_str))
        
        if not target_row:
             await query.answer("User not found", show_alert=True)
             return
             
        data_row = sheet.row_values(target_row)
        # 0:ID, 1:Name, 2:Username, 3:Link, 4:Balance, 5:LastUpdated
        name, username, link, balance, last = data_row[1], data_row[2], data_row[3], data_row[4], data_row[5]
        user_id = data_row[0]
        
        # Re-fetch Advance Payment breakdown
        breakdown_text = get_contribution_breakdown(target_row)
        
        msg = (
            f"💳 <b>Account Details</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 {name} {username}\n"
            f"🔑 {user_id}\n\n"
            f"💸 <b>Balance:</b> {format_money(balance)}\n"
            f"{breakdown_text}"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱️ Updated: {last}"
        )
        keyboard = [
            [InlineKeyboardButton("🧾 Transactions", callback_data=f"tx_{user_id}"),
             InlineKeyboardButton("✖️ Close", callback_data=f"close_{user_id}")]
        ]
        try:
            await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            pass  # Message unchanged, ignore error

    elif data.startswith("close_"):
        await query.edit_message_text("✨ Thanks for using River Bank!")

    # Handle transfer confirmations
    elif data.startswith("confirm_"):
        transfer_id = data.replace("confirm_", "")
        if transfer_id not in pending_transfers:
            await query.edit_message_text("✖️ This transfer has expired or already been processed.")
            return
        
        transfer_data = pending_transfers[transfer_id]
        
        # Verify the user is the sender
        if transfer_data["sender_id"] != user.id:
            await query.answer("This is not your transfer!", show_alert=True)
            return
        
        # Re-check balance (in case it changed)
        sender_balance = int(sheet.cell(transfer_data["sender_row"], 5).value)
        if sender_balance < transfer_data["amount"]:
            await query.edit_message_text("✖️ Insufficient balance. Transfer cancelled.")
            del pending_transfers[transfer_id]
            return
        
        # Process transfer
        amount = transfer_data["amount"]
        target_row = transfer_data["target_row"]
        target_name = transfer_data["target_name"]
        
        # Deduct from sender
        new_sender_balance = sender_balance - amount
        sheet.update_cell(transfer_data["sender_row"], 5, str(new_sender_balance))
        sheet.update_cell(transfer_data["sender_row"], 6, format_datetime())
        
        # Add sender transaction
        prev_tx = sheet.cell(transfer_data["sender_row"], 7).value
        new_tx = f"{format_datetime()} - {CURRENCY}{amount} {target_name}\n{prev_tx}" if prev_tx else f"{format_datetime()} - {CURRENCY}{amount} {target_name}"
        sheet.update_cell(transfer_data["sender_row"], 7, new_tx)
        
        # Add to receiver
        target_balance = int(sheet.cell(target_row, 5).value)
        new_target_balance = target_balance + amount
        sheet.update_cell(target_row, 5, str(new_target_balance))
        sheet.update_cell(target_row, 6, format_datetime())
        
        # Add receiver transaction
        sender_name = sheet.cell(transfer_data["sender_row"], 2).value
        prev_tx_target = sheet.cell(target_row, 7).value
        new_tx_target = f"{format_datetime()} + {CURRENCY}{amount} {sender_name}\n{prev_tx_target}" if prev_tx_target else f"{format_datetime()} + {CURRENCY}{amount} {sender_name}"
        sheet.update_cell(target_row, 7, new_tx_target)
        
        # Clean up
        del pending_transfers[transfer_id]
        
        await query.edit_message_text(
            f"✨ <b>Transfer Successful!</b>\n\n"
            f"Sent: <b>{CURRENCY}{amount}</b> to <b>{target_name}</b>\n"
            f"New balance: <b>{CURRENCY}{new_sender_balance}</b>",
            parse_mode="HTML"
        )
        await send_log(f"💸 {user.first_name} transferred {CURRENCY}{amount} to {target_name}", context)
    
    elif data.startswith("cancel_"):
        transfer_id = data.replace("cancel_", "")
        if transfer_id in pending_transfers:
            if pending_transfers[transfer_id]["sender_id"] != user.id:
                await query.answer("This is not your transfer!", show_alert=True)
                return
            del pending_transfers[transfer_id]
        await query.edit_message_text("✖️ Transfer cancelled.")

# ==================== INFOBANK ====================
async def infobank(update, context):
    all_data = sheet.get_all_records()
    total_users = len(all_data)
    total_value = sum(int(user["Balance"]) for user in all_data if user["Balance"])

    # Fetch Owner Name
    try:
        if OWNER_ID:
            owner_row = find_user_row(OWNER_ID)
            if owner_row:
                owner_name = sheet.cell(owner_row, 2).value
            else:
                chat = await context.bot.get_chat(OWNER_ID)
                owner_name = chat.first_name
        else:
            owner_name = "Owner"
    except Exception:
        owner_name = "Owner"
        
    owner_link = f"<a href='tg://user?id={OWNER_ID}'>{html.escape(owner_name)}</a>"

    # Fetch Managers
    managers_display = ""
    for username in ADMINS:
        row = find_user_by_username(username)
        if row:
            data = sheet.row_values(row)
            m_name = data[1]
            m_id = data[0]
            managers_display += f"• <a href='tg://user?id={m_id}'>{html.escape(m_name)}</a>\n"
        else:
            managers_display += f"• @{username}\n"

    msg = (
        f"owner: {owner_link}\n"
        f"managers:\n"
        f"{managers_display}\n"
        f"currency: {CURRENCY}\n"
        f"total accounts: {total_users}\n"
        f"total value: {CURRENCY}{total_value}"
    )
    sent_msg = await update.message.reply_text(msg, parse_mode="HTML")
    schedule_auto_delete(context, update.effective_chat.id, sent_msg.message_id)

# ==================== CHECK (ADMIN ONLY) ====================
async def check(update, context):
    if not can_modify(update.effective_user):
        await update.message.reply_text("✖️ Only the bank owner or managers can check other accounts.")
        return

    target_row = None
    # Case 1: Reply
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
        target_row = find_user_row(target.id)
        if not target_row:
             await update.message.reply_text("✖️ This user doesn't have an account.")
             return
    # Case 2: Mention
    elif context.args and context.args[0].startswith("@"):
        target_username = context.args[0]
        target_row = find_user_by_username(target_username)
        if not target_row:
            await update.message.reply_text(f"✖️ User {target_username} not found.")
            return
    else:
        await update.message.reply_text("Usage:\n• Reply: /check\n• Mention: /check @user")
        return

    # Display info
    data = sheet.row_values(target_row)
    # 0:ID, 1:Name, 2:Username, 3:Link, 4:Balance, 5:LastUpdated
    name, username, link, balance, last = data[1], data[2], data[3], data[4], data[5]
    user_id = data[0]

    # Get Advance Payment breakdown from column 8
    breakdown_text = get_contribution_breakdown(target_row)
    
    msg = (
        f"💳 <b>Account Details</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 {name} {username}\n"
        f"🔑 {user_id}\n\n"
        f"� <b>Balance:</b> {format_money(balance)}\n"
        f"{breakdown_text}"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ Updated: {last}"
    )
    
    keyboard = [
        [InlineKeyboardButton("� Transactions", callback_data=f"tx_{user_id}"),
         InlineKeyboardButton("✖️ Close", callback_data=f"close_{user_id}")]
    ]
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

# ==================== START/HELP ====================
async def start(update, context):
    user = update.effective_user
    msg = (
        f"🏦 <b>Welcome to {BANK_NAME}!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Hello, {user.first_name}! 👋\n\n"
        
        f"💳 <b>Account</b>\n"
        f"  /new — Create account\n"
        f"  /bal — Check balance\n\n"
        
        f" <b>Info</b>\n"
        f"  /infobank — Bank stats\n"
        f"  /help — This menu\n\n"
        
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔧 <b>Admin Commands</b>\n"
        f"  /add — Add balance (Reply or @user)\n"
        f"  /use — Deduct balance (Reply or @user)\n"
        f"  /check — View user info\n"
        f"  /transfer — Transfer (Admin Only)\n"
        f"  /prom — Promote (reply)\n"
        f"  /dem — Demote (reply)"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

# ==================== TRANSFER ====================
pending_transfers = {}  # Store pending transfer confirmations

async def transfer(update, context):
    user = update.effective_user
    
    # Restriction: Admin/Owner only
    if not can_modify(user):
        await update.message.reply_text("✖️ Transfers are disabled for regular users. You can only check your balance.")
        return

    sender_row = find_user_row(user.id)
    
    if not sender_row:
        await update.message.reply_text("✖️ You don't have an account yet. Use /new to create one.")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /transfer @username [amount]\nExample: /transfer @john 500")
        return
    
    target_username = context.args[0]
    try:
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("✖️ Invalid amount. Use a number.")
        return
    
    if amount <= 0:
        await update.message.reply_text("✖️ Amount must be greater than 0.")
        return
    
    # Find target user
    target_row = find_user_by_username(target_username)
    if not target_row:
        await update.message.reply_text(f"✖️ User {target_username} not found or doesn't have an account.")
        return
    
    # Get sender balance
    sender_balance = int(sheet.cell(sender_row, 5).value)
    if sender_balance < amount:
        await update.message.reply_text(f"✖️ Insufficient balance. You only have {CURRENCY}{sender_balance}.")
        return
    
    # Get target info
    target_data = sheet.row_values(target_row)
    target_name = target_data[1]
    target_id = target_data[0]
    
    # Check if sending to self
    if str(target_id) == str(user.id):
        await update.message.reply_text("✖️ You cannot transfer money to yourself.")
        return
    
    # Store pending transfer and ask for confirmation
    transfer_id = f"{user.id}_{target_id}_{amount}_{datetime.now().timestamp()}"
    pending_transfers[transfer_id] = {
        "sender_id": user.id,
        "sender_row": sender_row,
        "target_id": target_id,
        "target_row": target_row,
        "target_name": target_name,
        "amount": amount
    }
    
    keyboard = [
        [InlineKeyboardButton("✨ Confirm", callback_data=f"confirm_{transfer_id}"),
         InlineKeyboardButton("✖️ Cancel", callback_data=f"cancel_{transfer_id}")]
    ]
    
    msg = (
        f"📤 <b>Transfer Confirmation</b>\n\n"
        f"Send <b>{CURRENCY}{amount}</b> to <b>{target_name}</b>?\n\n"
        f"Your balance: {CURRENCY}{sender_balance}\n"
        f"After transfer: {CURRENCY}{sender_balance - amount}"
    )
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

# ==================== TOP/LEADERBOARD ====================



async def clear(update, context):
    """Clear a user's balance, transactions, and advance payments."""
    if not can_modify(update.effective_user):
        await update.message.reply_text("✖️ Only the bank owner or managers can clear accounts.")
        return

    target_row = None
    target_name = ""
    
    # Case 1: Reply
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
        target_row = find_user_row(target.id)
        target_name = target.first_name
        if not target_row:
            await update.message.reply_text("✖️ This user doesn't have an account.")
            return
    # Case 2: Mention
        if not target_row:
            await update.message.reply_text(f"✖️ User {target_username} not found.")
            return
        target_name = sheet.cell(target_row, 2).value
        # Need target ID to display
        target_id_val = sheet.cell(target_row, 1).value
        # For mention case, we need to fetch info
    else:
        await update.message.reply_text("Usage:\n• Reply: /clear\n• Mention: /clear @user")
        return

    # To be consistent with the requested format link - ID
    # Use find_user logic result
    # We need Name, ID.
    # In reply case: target object has .id, .first_name
    # In mention case: sheet has it.
    
    if update.message.reply_to_message:
        t_id = target.id
        t_name = target.first_name
    else:
        # Lookup from sheet
        vals = sheet.row_values(target_row)
        t_id = vals[0]
        t_name = vals[1]

    t_link = f"<a href='tg://user?id={t_id}'>{html.escape(t_name)}</a>"

    # Clear the data
    sheet.update_cell(target_row, 5, "0")  # Balance = 0
    sheet.update_cell(target_row, 6, format_datetime())  # Update timestamp
    sheet.update_cell(target_row, 7, "")  # Clear transaction history
    sheet.update_cell(target_row, 8, "")  # Clear advance payments

    await update.message.reply_text(
        f"account cleared for {t_link} — {t_id} ☑️",
        parse_mode="HTML"
    )
    await send_log(f"🗑️ {update.effective_user.first_name} cleared account for {t_name}", context)


# ==================== APP RUN ====================
app = ApplicationBuilder().token(BOT_TOKEN).build()

# Register command handlers
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", start))
app.add_handler(CommandHandler("new", new))
app.add_handler(CommandHandler("add", add))
app.add_handler(CommandHandler("use", use))
app.add_handler(CommandHandler("check", check))
app.add_handler(CommandHandler("prom", prom))
app.add_handler(CommandHandler("dem", dem))
app.add_handler(CommandHandler("bal", bal))
app.add_handler(CommandHandler("infobank", infobank))
app.add_handler(CommandHandler("transfer", transfer))

app.add_handler(CommandHandler("clear", clear))


# Register callback query handler for buttons
app.add_handler(CallbackQueryHandler(button_callback))

# Start the bot
print("✨ River Bank Bot is now running...")
keep_alive.keep_alive()
app.run_polling()