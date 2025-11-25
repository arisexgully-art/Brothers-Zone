import asyncio
import logging
import sqlite3
import aiohttp
import re
import os
import sys
import hashlib
from datetime import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatType, ChatMemberStatus
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton,
    ReplyKeyboardRemove
)

# ================= CONFIGURATION =================

BOT_TOKEN = "8070506568:AAE6mUi2wcXMRTnZRwHUut66Nlu1NQC8Opo"
ADMIN_IDS = [8308179143, 5085250851]

# Channel Config
CHANNEL_1_ID = "@your_first_channel" 
CHANNEL_1_LINK = "https://t.me/your_first_channel"
CHANNEL_2_ID = "@your_second_channel"
CHANNEL_2_LINK = "https://t.me/your_second_channel"

# API Settings
API_TOKEN = "Rk5CRTSGcX9fh1WHeIVxYViVlEhaUmSDXG1Qe1dOc2ZykmZGiw=="
API_URL = "http://51.77.216.195/crapi/dgroup/viewstats"

# Group ID
GROUP_ID = -1003472422744

# =================================================

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# --- Helper Functions ---
def safe_print(text):
    try: print(text)
    except: pass

async def safe_answer(callback: types.CallbackQuery, text: str = None, alert: bool = False):
    try:
        if text: await callback.answer(text, show_alert=alert)
        else: await callback.answer()
    except: pass

# --- DATABASE SETUP (UPDATED) ---
def init_db():
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS countries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        )
    """)
    
    # Updated Numbers Table: Added 'assigned_to' column
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS numbers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_id INTEGER,
            number TEXT UNIQUE,
            status INTEGER DEFAULT 0,
            assigned_to INTEGER DEFAULT NULL
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT UNIQUE,
            invite_link TEXT
        )
    """)

    # New Table: To track received SMS and prevent duplicates/misses
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS processed_sms (
            signature TEXT PRIMARY KEY,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()

init_db()

# --- FSM States ---
class AdminStates(StatesGroup):
    waiting_country_name = State()
    waiting_number_input = State()
    waiting_broadcast_msg = State()
    waiting_channel_id = State()
    waiting_channel_link = State()
    last_msg_id = State()

# --- Channel Check ---
async def check_subscription(user_id):
    if user_id in ADMIN_IDS: return True
    conn = sqlite3.connect("bot_database.db")
    channels = conn.cursor().execute("SELECT chat_id FROM channels").fetchall()
    conn.close()
    if not channels: return True
    for ch in channels:
        try:
            member = await bot.get_chat_member(chat_id=ch[0], user_id=user_id)
            if member.status not in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                return False
        except: pass 
    return True

def get_join_keyboard():
    conn = sqlite3.connect("bot_database.db")
    channels = conn.cursor().execute("SELECT invite_link FROM channels").fetchall()
    conn.close()
    kb = []
    for i, ch in enumerate(channels):
        kb.append([InlineKeyboardButton(text=f"📢 Join Channel {i+1}", url=ch[0])])
    kb.append([InlineKeyboardButton(text="✅ VERIFY JOIN", callback_data="verify_join")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

# --- API Function (Robust) ---
async def check_otp_api(phone_number):
    clean_number = ''.join(filter(str.isdigit, str(phone_number)))
    params = {'token': API_TOKEN, 'filternum': clean_number, 'records': 50}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124 Safari/537.36"}
    timeout = aiohttp.ClientTimeout(total=15)
    
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False), timeout=timeout) as session:
            async with session.get(API_URL, params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "success" and data.get("data"):
                        return data["data"]
    except Exception as e: 
        safe_print(f"API Error for {phone_number}: {e}")
    return []

# --- Keyboards ---
def get_admin_reply_keyboard():
    kb = [
        [KeyboardButton(text="ADD COUNTRY"), KeyboardButton(text="REMOVE COUNTRY")],
        [KeyboardButton(text="ADD NUMBER"), KeyboardButton(text="📢 BROADCAST")],
        [KeyboardButton(text="ADD CHANNEL"), KeyboardButton(text="REMOVE CHANNEL")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_country_inline_keyboard():
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM countries")
    countries = cursor.fetchall()
    buttons = []
    for c_id, c_name in countries:
        cnt = cursor.execute("SELECT COUNT(*) FROM numbers WHERE country_id = ? AND status = 0", (c_id,)).fetchone()[0]
        buttons.append([InlineKeyboardButton(text=f"{c_name} ({cnt})", callback_data=f"buy_{c_id}_{c_name}")])
    conn.close()
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- START & VERIFY ---

@dp.message(Command("start"), F.chat.type == ChatType.PRIVATE)
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    
    conn = sqlite3.connect("bot_database.db")
    try: conn.cursor().execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)); conn.commit()
    except: pass
    
    # RESET USER: If user sends /start, unassign their previous numbers to avoid confusion?
    # Or keep checking? Usually /start means reset.
    # Let's free up numbers assigned to this user if they haven't received OTP?
    # For now, we just let the background task handle running numbers.
    
    # If user explicitly cancels, we free. Here just menu.
    conn.close()

    if not await check_subscription(user_id):
        await message.answer("⚠️ **Please join our channels:**", reply_markup=get_join_keyboard())
        return

    if user_id in ADMIN_IDS:
        await message.answer("👑 Admin Panel:", reply_markup=get_admin_reply_keyboard())
        kb = get_country_inline_keyboard()
        await message.answer("User View:", reply_markup=kb if kb.inline_keyboard else None)
    else:
        kb = get_country_inline_keyboard()
        if not kb.inline_keyboard: await message.answer("Service Unavailable.", reply_markup=ReplyKeyboardRemove())
        else: await message.answer("Select Country:", reply_markup=kb)

@dp.callback_query(F.data == "verify_join")
async def verify_join_handler(callback: types.CallbackQuery, state: FSMContext):
    if await check_subscription(callback.from_user.id):
        await safe_answer(callback, text="Verified!")
        await callback.message.delete()
        await cmd_start(callback.message, state)
    else:
        await safe_answer(callback, text="Join First!", alert=True)

# --- USER FLOW ---

@dp.callback_query(F.data.startswith("buy_"))
async def user_buy_number(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    await safe_answer(callback)
    
    if not await check_subscription(user_id):
        await safe_answer(callback, text="Join Channels!", alert=True)
        return

    part = callback.data.split("_")
    c_id, c_name = part[1], part[2]
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    
    # 1. Cancel previous number for this user (Make it free again)
    cursor.execute("UPDATE numbers SET status = 0, assigned_to = NULL WHERE assigned_to = ?", (user_id,))
    
    # 2. Assign new number
    res = cursor.execute("SELECT number FROM numbers WHERE country_id = ? AND status = 0 LIMIT 1", (c_id,)).fetchone()
    
    if not res:
        conn.commit()
        conn.close()
        await safe_answer(callback, text="Stock Empty!", alert=True)
        return
        
    phone = res[0]
    # Assign specific number to specific user
    cursor.execute("UPDATE numbers SET status = 1, assigned_to = ? WHERE number = ?", (user_id, phone))
    conn.commit()
    conn.close()
    
    text = f"🌎 {c_name} Assigned:\n<code>+{phone}</code>\n\nWaiting for OTP..."
    kb = [[InlineKeyboardButton(text="CHANGE NUMBER", callback_data=f"buy_{c_id}_{c_name}")], [InlineKeyboardButton(text="CHANGE COUNTRY", callback_data="show_country_list")], [InlineKeyboardButton(text="CANCEL", callback_data="cancel_op")]]
    
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    safe_print(f"Assigned {phone} to User {user_id}")

@dp.callback_query(F.data == "show_country_list")
async def show_list(callback: types.CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    
    # Free up number
    conn = sqlite3.connect("bot_database.db")
    conn.cursor().execute("UPDATE numbers SET status = 0, assigned_to = NULL WHERE assigned_to = ?", (callback.from_user.id,))
    conn.commit()
    conn.close()
    
    kb = get_country_inline_keyboard()
    await callback.message.edit_text("Select Country:", reply_markup=kb)

@dp.callback_query(F.data == "cancel_op")
async def cancel_op(callback: types.CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    
    # Free up number
    conn = sqlite3.connect("bot_database.db")
    conn.cursor().execute("UPDATE numbers SET status = 0, assigned_to = NULL WHERE assigned_to = ?", (callback.from_user.id,))
    conn.commit()
    conn.close()
    
    await callback.message.delete()
    await cmd_start(callback.message, state)

@dp.callback_query(F.data == "back_home")
async def go_back(callback: types.CallbackQuery, state: FSMContext):
    await cancel_op(callback, state)

# --- ADMIN HANDLERS (Condensed) ---
@dp.message(F.text == "ADD CHANNEL", F.from_user.id.in_(ADMIN_IDS))
async def ach(m: types.Message, s: FSMContext):
    msg=await m.answer("Channel ID:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="back_home")]]))
    await s.update_data(last_msg_id=msg.message_id); await s.set_state(AdminStates.waiting_channel_id)

@dp.message(AdminStates.waiting_channel_id)
async def ach_id(m: types.Message, s: FSMContext):
    await s.update_data(chat_id=m.text.strip())
    d=await s.get_data()
    try: await bot.edit_message_text(chat_id=m.chat.id, message_id=d['last_msg_id'], text="Invite Link:")
    except: await m.answer("Invite Link:")
    await m.delete(); await s.set_state(AdminStates.waiting_channel_link)

@dp.message(AdminStates.waiting_channel_link)
async def ach_save(m: types.Message, s: FSMContext):
    d=await s.get_data()
    conn=sqlite3.connect("bot_database.db")
    try: conn.cursor().execute("INSERT INTO channels (chat_id, invite_link) VALUES (?, ?)", (d['chat_id'], m.text.strip())); conn.commit(); res="✅ Added."
    except: res="❌ Exists."
    conn.close(); await m.delete()
    try: await bot.edit_message_text(chat_id=m.chat.id, message_id=d['last_msg_id'], text=res)
    except: await m.answer(res)
    await s.clear()

@dp.message(F.text == "REMOVE CHANNEL", F.from_user.id.in_(ADMIN_IDS))
async def rch(m: types.Message):
    conn=sqlite3.connect("bot_database.db")
    chs=conn.cursor().execute("SELECT id, chat_id FROM channels").fetchall()
    conn.close()
    btns=[[InlineKeyboardButton(text=f"❌ {c[1]}", callback_data=f"del_ch_{c[0]}")] for c in chs]
    btns.append([InlineKeyboardButton(text="Cancel", callback_data="back_home")])
    await m.answer("Remove:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("del_ch_"))
async def dch(c: types.CallbackQuery):
    await safe_answer(c)
    conn=sqlite3.connect("bot_database.db")
    conn.cursor().execute("DELETE FROM channels WHERE id=?", (c.data.split("_")[2],))
    conn.commit(); conn.close()
    await c.message.edit_text("✅ Removed.")

# (Other Admin handlers like ADD/REMOVE COUNTRY/NUMBER/BROADCAST are assumed same as previous)
# I'm skipping repeating them to save space, assuming you have them from previous working code. 
# If you need them again, let me know. I'll just include the polling logic below which is the main fix.

@dp.message(F.text == "ADD COUNTRY", F.from_user.id.in_(ADMIN_IDS))
async def ac_start(m: types.Message, s: FSMContext):
    msg=await m.answer("Country Name:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="back_home")]]))
    await s.update_data(last_msg_id=msg.message_id); await s.set_state(AdminStates.waiting_country_name)
@dp.message(AdminStates.waiting_country_name)
async def ac_save(m: types.Message, s: FSMContext):
    conn=sqlite3.connect("bot_database.db")
    try: conn.cursor().execute("INSERT INTO countries (name) VALUES (?)", (m.text.strip(),)); conn.commit(); res="✅ Added."
    except: res="❌ Exists."
    conn.close(); await m.delete()
    d=await s.get_data()
    try: await bot.edit_message_text(chat_id=m.chat.id, message_id=d['last_msg_id'], text=res)
    except: await m.answer(res)
    await s.clear()
@dp.message(F.text == "REMOVE COUNTRY", F.from_user.id.in_(ADMIN_IDS))
async def rc_start(m: types.Message):
    conn=sqlite3.connect("bot_database.db")
    cs=conn.cursor().execute("SELECT id, name FROM countries").fetchall()
    conn.close()
    btns=[[InlineKeyboardButton(text=f"❌ {c[1]}", callback_data=f"del_c_{c[0]}")] for c in cs]
    btns.append([InlineKeyboardButton(text="Cancel", callback_data="back_home")])
    await m.answer("Remove:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
@dp.callback_query(F.data.startswith("del_c_"))
async def rc_act(c: types.CallbackQuery):
    await safe_answer(c); cid=c.data.split("_")[2]
    conn=sqlite3.connect("bot_database.db")
    conn.cursor().execute("DELETE FROM countries WHERE id=?", (cid,))
    conn.cursor().execute("DELETE FROM numbers WHERE country_id=?", (cid,))
    conn.commit(); conn.close()
    await c.message.edit_text("✅ Removed.")
@dp.message(F.text == "ADD NUMBER", F.from_user.id.in_(ADMIN_IDS))
async def an_start(m: types.Message):
    conn=sqlite3.connect("bot_database.db")
    cs=conn.cursor().execute("SELECT id, name FROM countries").fetchall()
    conn.close()
    btns=[[InlineKeyboardButton(text=c[1], callback_data=f"sel_cn_{c[0]}_{c[1]}")] for c in cs]
    btns.append([InlineKeyboardButton(text="Cancel", callback_data="back_home")])
    await m.answer("Select:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
@dp.callback_query(F.data.startswith("sel_cn_"))
async def an_sel(c: types.CallbackQuery, s: FSMContext):
    await safe_answer(c); p=c.data.split("_")
    await s.update_data(country_id=p[2], country_name=p[3])
    btns=[[InlineKeyboardButton(text="📂 File", callback_data="in_file")], [InlineKeyboardButton(text="✍️ Written", callback_data="in_text")], [InlineKeyboardButton(text="🔙 Cancel", callback_data="back_home")]]
    msg=await c.message.edit_text(f"Sel: {p[3]}\nMethod:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
    await s.update_data(last_msg_id=msg.message_id)
@dp.callback_query(F.data.in_({"in_file", "in_text"}))
async def an_inp(c: types.CallbackQuery, s: FSMContext):
    await safe_answer(c); await s.update_data(mode=c.data)
    msg=await c.message.edit_text("Input:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="back_home")]]))
    await s.update_data(last_msg_id=msg.message_id); await s.set_state(AdminStates.waiting_number_input)
@dp.message(AdminStates.waiting_number_input)
async def an_proc(m: types.Message, s: FSMContext):
    d=await s.get_data(); c=""; 
    if d['mode']=="in_file" and m.document: 
        f=await bot.get_file(m.document.file_id); c=(await bot.download_file(f.file_path)).read().decode('utf-8')
    elif d['mode']=="in_text": c=m.text
    else: return
    nums=[n.strip() for n in re.split(r'[,\n\r]+', c) if n.strip()]
    conn=sqlite3.connect("bot_database.db"); added=0
    for n in nums:
        try: conn.cursor().execute("INSERT INTO numbers (country_id, number, status, assigned_to) VALUES (?, ?, 0, NULL)", (d['country_id'], n)); added+=1
        except: conn.cursor().execute("UPDATE numbers SET status=0, assigned_to=NULL WHERE number=? AND country_id=?", (n, d['country_id'])); added+=1
    conn.commit(); conn.close(); await m.delete()
    try: await bot.edit_message_text(chat_id=m.chat.id, message_id=d['last_msg_id'], text=f"✅ Added {added}.")
    except: await m.answer(f"✅ Added {added}.")
    await s.clear()
@dp.message(F.text == "📢 BROADCAST", F.from_user.id.in_(ADMIN_IDS))
async def bc_start(m: types.Message, s: FSMContext):
    msg=await m.answer("Msg:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="back_home")]]))
    await s.update_data(last_msg_id=msg.message_id); await s.set_state(AdminStates.waiting_broadcast_msg)
@dp.message(AdminStates.waiting_broadcast_msg)
async def bc_send(m: types.Message, s: FSMContext):
    conn=sqlite3.connect("bot_database.db"); us=conn.cursor().execute("SELECT user_id FROM users").fetchall(); conn.close()
    cnt=0; sts=await m.answer("Sending...")
    for u in us:
        try: await bot.send_message(u[0], m.text); cnt+=1; await asyncio.sleep(0.05)
        except: pass
    await sts.edit_text(f"✅ Sent: {cnt}"); await s.clear()

# ================= CENTRALIZED BACKGROUND TASK (THE FIX) =================
# This single loop checks all active numbers and routes messages to the correct user.

async def master_polling_loop():
    safe_print("🚀 Master Polling Loop Started...")
    while True:
        try:
            # 1. Get all active numbers assigned to users (status = 1)
            conn = sqlite3.connect("bot_database.db")
            active_orders = conn.cursor().execute("SELECT assigned_to, number, country_id FROM numbers WHERE status = 1").fetchall()
            
            # Map country_id to country_name for efficiency
            countries = {row[0]: row[1] for row in conn.cursor().execute("SELECT id, name FROM countries").fetchall()}
            conn.close()

            if not active_orders:
                await asyncio.sleep(3)
                continue

            # 2. Check API for each active number
            for user_id, phone, c_id in active_orders:
                country_name = countries.get(c_id, "Unknown")
                
                # API call
                msgs = await check_otp_api(phone)
                
                if msgs:
                    for msg in msgs:
                        # Unique signature to prevent duplicates
                        sig = hashlib.md5(f"{msg['dt']}{msg['message']}{phone}".encode()).hexdigest()
                        
                        # Check if already processed
                        conn = sqlite3.connect("bot_database.db")
                        exists = conn.cursor().execute("SELECT 1 FROM processed_sms WHERE signature = ?", (sig,)).fetchone()
                        
                        if not exists:
                            # Mark as processed
                            conn.cursor().execute("INSERT INTO processed_sms (signature) VALUES (?)", (sig,))
                            conn.commit()
                            
                            # Prepare Message
                            msg_body = msg.get("message", "")
                            svc = msg.get("cli", "Service")
                            svc = svc.capitalize() if svc and svc != "null" else "Unknown"
                            
                            # Enhanced Regex
                            otp_match = re.search(r'(?:\d{3}[-\s]\d{3})|(?<!\d)\d{4,8}(?!\d)', msg_body)
                            otp = otp_match.group(0) if otp_match else "N/A"
                            
                            ctime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            masked = f"{phone[:4]}***{phone[-4:]}" if len(phone) > 7 else phone
                            
                            utxt = f"🌎 Country : {country_name}\n🔢 Number : <code>{phone}</code>\n🔑 OTP : <code>{otp}</code>\n💸 Reward: 🔥"
                            gtxt = f"✅ {country_name} {svc} OTP Received!\n━━━━━━━━━━━━━━━━━━━━\n📱 Number: <code>{masked}</code>\n🌍 Country: {country_name}\n⚙️ Service: {svc}\n🔒 OTP Code: <code>{otp}</code>\n⏳ Time: {ctime}\n━━━━━━━━━━━━━━━━━━━━\nMessage:\n{msg_body}"
                            
                            safe_print(f"✅ OTP for User {user_id} on {phone}")
                            
                            # Send to specific user who owns the number
                            try: await bot.send_message(user_id, utxt)
                            except Exception as e: safe_print(f"User Send Err: {e}")
                            
                            # Send to Group
                            try: await bot.send_message(GROUP_ID, gtxt)
                            except: pass
                        
                        conn.close()
                
                # Rate limiting between numbers
                await asyncio.sleep(1.5) 

            # Wait before next cycle
            await asyncio.sleep(2)

        except Exception as e:
            safe_print(f"Master Loop Error: {e}")
            await asyncio.sleep(5)

# --- SERVER & MAIN ---
async def web_handler(request): return web.Response(text="Bot Running")
async def start_web_server():
    app = web.Application(); app.router.add_get('/', web_handler)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 8080))).start()

async def main():
    safe_print("System Starting...")
    
    # Start Background Tasks
    asyncio.create_task(start_web_server())
    asyncio.create_task(master_polling_loop()) # The Global Checker
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
