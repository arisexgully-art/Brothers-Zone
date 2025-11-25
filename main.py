import asyncio
import logging
import sqlite3
import aiohttp
import re
import os
import sys
import hashlib
import html
import random
import time
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

# --- DATABASE SETUP (WAL MODE ADDED) ---
def init_db():
    conn = sqlite3.connect("bot_database.db")
    # Enable Write-Ahead Logging for concurrency (Fixes DB Lock issues)
    conn.execute("PRAGMA journal_mode=WAL;") 
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS countries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        )
    """)
    
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

# --- Channel Subscription Check ---
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

# --- API Function (NUCLEAR FIX: Fresh Session Every Time) ---
async def check_otp_api(phone_number):
    clean_number = ''.join(filter(str.isdigit, str(phone_number)))
    
    params = {
        'token': API_TOKEN, 
        'filternum': clean_number, 
        'records': 50,
        't': int(time.time() * 1000) # Force new request
    }
    
    headers = {
        "User-Agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/{random.randint(100, 130)}.0.0.0 Safari/537.36",
        "Connection": "close" # Ensure connection closes
    }
    
    # Create a BRAND NEW session for every single check
    # This guarantees no "stuck" connections
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(API_URL, params=params, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "success" and data.get("data"):
                        return data["data"]
    except Exception as e: 
        safe_print(f"⚠️ API Fail {phone_number}: {e}")
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
    
    # Delete active session on start
    conn.cursor().execute("DELETE FROM numbers WHERE assigned_to = ?", (user_id,))
    conn.commit()
    conn.close()

    if not await check_subscription(user_id):
        await message.answer("⚠️ **Please join our channels first:**", reply_markup=get_join_keyboard())
        return

    if user_id in ADMIN_IDS:
        await message.answer("👑 Admin Panel:", reply_markup=get_admin_reply_keyboard())
    
    kb = get_country_inline_keyboard()
    if not kb.inline_keyboard: 
        await message.answer("No services available.", reply_markup=ReplyKeyboardRemove() if user_id not in ADMIN_IDS else None)
    else: 
        await message.answer("Select Country:", reply_markup=kb)

@dp.callback_query(F.data == "verify_join")
async def verify_join_handler(callback: types.CallbackQuery, state: FSMContext):
    if await check_subscription(callback.from_user.id):
        await safe_answer(callback, text="Verified!")
        await callback.message.delete()
        await cmd_start(callback.message, state)
    else:
        await safe_answer(callback, text="Join First!", alert=True)

# --- USER FLOW (With DELETE logic) ---

@dp.callback_query(F.data.startswith("buy_"))
async def user_buy_number(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    await safe_answer(callback)
    
    if not await check_subscription(user_id):
        await safe_answer(callback, text="Join Channels!", alert=True)
        return

    part = callback.data.split("_")
    c_id, c_name = part[1], part[2]
    
    try:
        await callback.message.edit_text(f"🔄 <b>Finding fresh number for {c_name}...</b>", reply_markup=None)
    except: pass

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    
    # Completely DELETE previous number
    cursor.execute("DELETE FROM numbers WHERE assigned_to = ?", (user_id,))
    conn.commit()
    
    assigned_phone = None
    
    for _ in range(5):
        row = cursor.execute("SELECT id, number FROM numbers WHERE country_id = ? AND status = 0 ORDER BY RANDOM() LIMIT 1", (c_id,)).fetchone()
        if not row: break
            
        num_id, phone = row
        cursor.execute("UPDATE numbers SET status = 1, assigned_to = ? WHERE id = ? AND status = 0", (user_id, num_id))
        
        if cursor.rowcount > 0:
            conn.commit()
            assigned_phone = phone
            break
        else: continue
            
    conn.close()
    
    if not assigned_phone:
        kb = [[InlineKeyboardButton(text="🔁 TRY AGAIN", callback_data=f"buy_{c_id}_{c_name}")], 
              [InlineKeyboardButton(text="🔙 BACK", callback_data="show_country_list")]]
        try: await callback.message.edit_text(f"⚠️ <b>Stock Empty or Busy for {c_name}!</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
        except: pass
        return
    
    text = f"🌎 {c_name} Assigned:\n<code>+{assigned_phone}</code>\n\nWaiting for OTP..."
    kb = [
        [InlineKeyboardButton(text="🔄 CHANGE NUMBER", callback_data=f"buy_{c_id}_{c_name}")], 
        [InlineKeyboardButton(text="🌍 CHANGE COUNTRY", callback_data="show_country_list")], 
        [InlineKeyboardButton(text="❌ CANCEL", callback_data="cancel_op")]
    ]
    
    await asyncio.sleep(0.5)
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data == "show_country_list")
async def show_list(callback: types.CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    conn = sqlite3.connect("bot_database.db")
    conn.cursor().execute("DELETE FROM numbers WHERE assigned_to = ?", (callback.from_user.id,))
    conn.commit()
    conn.close()
    kb = get_country_inline_keyboard()
    await callback.message.edit_text("Select Country:", reply_markup=kb)

@dp.callback_query(F.data == "cancel_op")
async def cancel_op(callback: types.CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    conn = sqlite3.connect("bot_database.db")
    conn.cursor().execute("DELETE FROM numbers WHERE assigned_to = ?", (callback.from_user.id,))
    conn.commit()
    conn.close()
    await callback.message.delete()
    await cmd_start(callback.message, state)

@dp.callback_query(F.data == "back_home")
async def go_back(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await cancel_op(callback, state)

# ================= ADMIN HANDLERS =================

@dp.message(F.text == "ADD CHANNEL", F.from_user.id.in_(ADMIN_IDS))
async def ach(m: types.Message, state: FSMContext):
    await state.clear()
    msg = await m.answer("Channel ID/Username:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="back_home")]]))
    await state.update_data(last_msg_id=msg.message_id)
    await state.set_state(AdminStates.waiting_channel_id)

@dp.message(AdminStates.waiting_channel_id)
async def ach_id(m: types.Message, state: FSMContext):
    await state.update_data(chat_id=m.text.strip())
    d = await state.get_data()
    try: await bot.edit_message_text(chat_id=m.chat.id, message_id=d['last_msg_id'], text=f"Channel: {m.text}\nNow Send Invite Link:")
    except: await m.answer("Invite Link:")
    await m.delete()
    await state.set_state(AdminStates.waiting_channel_link)

@dp.message(AdminStates.waiting_channel_link)
async def ach_save(m: types.Message, state: FSMContext):
    d = await state.get_data()
    conn = sqlite3.connect("bot_database.db")
    try: 
        conn.cursor().execute("INSERT INTO channels (chat_id, invite_link) VALUES (?, ?)", (d['chat_id'], m.text.strip()))
        conn.commit()
        res = "✅ Channel Added."
    except: res = "❌ Exists/Error."
    conn.close()
    await m.delete()
    try: await bot.edit_message_text(chat_id=m.chat.id, message_id=d['last_msg_id'], text=res)
    except: await m.answer(res)
    await state.clear()

@dp.message(F.text == "REMOVE CHANNEL", F.from_user.id.in_(ADMIN_IDS))
async def rch(m: types.Message, state: FSMContext):
    await state.clear()
    conn = sqlite3.connect("bot_database.db")
    chs = conn.cursor().execute("SELECT id, chat_id FROM channels").fetchall()
    conn.close()
    if not chs:
        await m.answer("No channels found.")
        return
    btns = [[InlineKeyboardButton(text=f"❌ {c[1]}", callback_data=f"del_ch_{c[0]}")] for c in chs]
    btns.append([InlineKeyboardButton(text="Cancel", callback_data="back_home")])
    await m.answer("Remove Channel:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("del_ch_"))
async def dch(c: types.CallbackQuery):
    await safe_answer(c)
    conn = sqlite3.connect("bot_database.db")
    conn.cursor().execute("DELETE FROM channels WHERE id=?", (c.data.split("_")[2],))
    conn.commit()
    conn.close()
    await c.message.edit_text("✅ Removed.")

@dp.message(F.text == "ADD COUNTRY", F.from_user.id.in_(ADMIN_IDS))
async def ac_start(m: types.Message, state: FSMContext):
    await state.clear()
    msg = await m.answer("Input Country Name:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="back_home")]]))
    await state.update_data(last_msg_id=msg.message_id)
    await state.set_state(AdminStates.waiting_country_name)

@dp.message(AdminStates.waiting_country_name)
async def ac_save(m: types.Message, state: FSMContext):
    conn = sqlite3.connect("bot_database.db")
    try: 
        conn.cursor().execute("INSERT INTO countries (name) VALUES (?)", (m.text.strip(),))
        conn.commit()
        res = f"✅ Country '{m.text}' Added."
    except: res = "❌ Already Exists."
    conn.close()
    await m.delete()
    d = await state.get_data()
    try: await bot.edit_message_text(chat_id=m.chat.id, message_id=d['last_msg_id'], text=res)
    except: await m.answer(res)
    await state.clear()

@dp.message(F.text == "REMOVE COUNTRY", F.from_user.id.in_(ADMIN_IDS))
async def rc_start(m: types.Message, state: FSMContext):
    await state.clear()
    conn = sqlite3.connect("bot_database.db")
    cs = conn.cursor().execute("SELECT id, name FROM countries").fetchall()
    conn.close()
    if not cs:
        await m.answer("No countries found.")
        return
    btns = [[InlineKeyboardButton(text=f"❌ {c[1]}", callback_data=f"del_c_{c[0]}")] for c in cs]
    btns.append([InlineKeyboardButton(text="Cancel", callback_data="back_home")])
    await m.answer("Remove Country:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("del_c_"))
async def rc_act(c: types.CallbackQuery):
    await safe_answer(c)
    cid = c.data.split("_")[2]
    conn = sqlite3.connect("bot_database.db")
    conn.cursor().execute("DELETE FROM countries WHERE id=?", (cid,))
    conn.cursor().execute("DELETE FROM numbers WHERE country_id=?", (cid,))
    conn.commit()
    conn.close()
    await c.message.edit_text("✅ Country & Numbers Removed.")

@dp.message(F.text == "ADD NUMBER", F.from_user.id.in_(ADMIN_IDS))
async def an_start(m: types.Message, state: FSMContext):
    await state.clear()
    conn = sqlite3.connect("bot_database.db")
    cs = conn.cursor().execute("SELECT id, name FROM countries").fetchall()
    conn.close()
    if not cs:
        await m.answer("Add a Country first!")
        return
    btns = [[InlineKeyboardButton(text=c[1], callback_data=f"sel_cn_{c[0]}_{c[1]}")] for c in cs]
    btns.append([InlineKeyboardButton(text="Cancel", callback_data="back_home")])
    await m.answer("Select Country for Numbers:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("sel_cn_"))
async def an_sel(c: types.CallbackQuery, state: FSMContext):
    await safe_answer(c)
    p = c.data.split("_")
    await state.update_data(country_id=p[2], country_name=p[3])
    btns = [[InlineKeyboardButton(text="📂 File", callback_data="in_file")], [InlineKeyboardButton(text="✍️ Written", callback_data="in_text")], [InlineKeyboardButton(text="🔙 Cancel", callback_data="back_home")]]
    msg = await c.message.edit_text(f"Selected: {p[3]}\nChoose Input Method:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
    await state.update_data(last_msg_id=msg.message_id)

@dp.callback_query(F.data.in_({"in_file", "in_text"}))
async def an_inp(c: types.CallbackQuery, state: FSMContext):
    await safe_answer(c)
    await state.update_data(mode=c.data)
    msg = await c.message.edit_text("Send numbers (One per line or Comma separated):", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="back_home")]]))
    await state.update_data(last_msg_id=msg.message_id)
    await state.set_state(AdminStates.waiting_number_input)

@dp.message(AdminStates.waiting_number_input)
async def an_proc(m: types.Message, state: FSMContext):
    d = await state.get_data()
    content = ""
    
    if d['mode'] == "in_file" and m.document: 
        f = await bot.get_file(m.document.file_id)
        content = (await bot.download_file(f.file_path)).read().decode('utf-8')
    elif d['mode'] == "in_text" and m.text: 
        content = m.text
    else: 
        await m.answer("Invalid input. Please send text or file.")
        return

    nums = [n.strip() for n in re.split(r'[,\n\r\s]+', content) if n.strip().isdigit()]
    
    if not nums:
        await m.answer("No valid numbers found.")
        return

    conn = sqlite3.connect("bot_database.db")
    added = 0
    for n in nums:
        try: 
            conn.cursor().execute("INSERT INTO numbers (country_id, number, status, assigned_to) VALUES (?, ?, 0, NULL)", (d['country_id'], n))
            added += 1
        except: 
            conn.cursor().execute("UPDATE numbers SET status=0, assigned_to=NULL WHERE number=? AND country_id=?", (n, d['country_id']))
            added += 1
    
    conn.commit()
    conn.close()
    await m.delete()
    
    msg_text = f"✅ Added/Reset {added} numbers to {d.get('country_name')}."
    try: await bot.edit_message_text(chat_id=m.chat.id, message_id=d['last_msg_id'], text=msg_text)
    except: await m.answer(msg_text)
    await state.clear()

@dp.message(F.text == "📢 BROADCAST", F.from_user.id.in_(ADMIN_IDS))
async def bc_start(m: types.Message, state: FSMContext):
    await state.clear()
    msg = await m.answer("Send Broadcast Message:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="back_home")]]))
    await state.update_data(last_msg_id=msg.message_id)
    await state.set_state(AdminStates.waiting_broadcast_msg)

@dp.message(AdminStates.waiting_broadcast_msg)
async def bc_send(m: types.Message, state: FSMContext):
    conn = sqlite3.connect("bot_database.db")
    us = conn.cursor().execute("SELECT user_id FROM users").fetchall()
    conn.close()
    
    cnt = 0
    sts = await m.answer("📢 Sending broadcast...")
    
    for u in us:
        try: 
            await bot.copy_message(chat_id=u[0], from_chat_id=m.chat.id, message_id=m.message_id)
            cnt += 1
            await asyncio.sleep(0.05)
        except: pass
        
    await sts.edit_text(f"✅ Broadcast Sent to {cnt} users.")
    await state.clear()

# ================= ROBUST MASTER POLLING (DEBUG ENABLED) =================

async def process_number_task(user_id, phone, c_id, countries):
    try:
        # Check API with fresh session
        msgs = await check_otp_api(phone)
        
        if not msgs: return

        # Loop through all messages without strict sorting first
        for msg in msgs:
            msg_body = msg.get("message", "")
            if not msg_body: continue

            # Robust Signature (Body + Timestamp)
            sig_raw = f"{msg.get('dt', '')}{msg_body}{phone}"
            sig = hashlib.md5(sig_raw.encode()).hexdigest()
            
            conn = sqlite3.connect("bot_database.db")
            cursor = conn.cursor()
            exists = cursor.execute("SELECT 1 FROM processed_sms WHERE signature = ?", (sig,)).fetchone()
            
            if not exists:
                cursor.execute("INSERT INTO processed_sms (signature) VALUES (?)", (sig,))
                conn.commit()
                
                country_name = countries.get(c_id, "Unknown")
                svc = msg.get("cli", "Service")
                svc = svc.capitalize() if svc and svc != "null" else "Unknown"
                
                otp_match = re.search(r'(?:\d{3}[-\s]\d{3})|(?<!\d)\d{4,8}(?!\d)', msg_body)
                otp = otp_match.group(0) if otp_match else "N/A"
                
                safe_msg = html.escape(msg_body)
                ctime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                masked = f"{phone[:4]}***{phone[-4:]}" if len(phone) > 7 else phone
                
                utxt = f"🌎 Country : {country_name}\n🔢 Number : <code>{phone}</code>\n🔑 OTP : <code>{otp}</code>\n💸 Reward: 🔥"
                gtxt = f"✅ {country_name} {svc} OTP Received!\n━━━━━━━━━━━━━━━━━━━━\n📱 Number: <code>{masked}</code>\n🌍 Country: {country_name}\n⚙️ Service: {svc}\n🔒 OTP Code: <code>{otp}</code>\n⏳ Time: {ctime}\n━━━━━━━━━━━━━━━━━━━━\nMessage:\n{safe_msg}"
                
                safe_print(f"✅ FOUND OTP for {user_id} on {phone}: {otp}")
                
                try: await bot.send_message(user_id, utxt)
                except: pass
                try: await bot.send_message(GROUP_ID, gtxt)
                except: pass
            
            conn.close()

    except Exception as e:
        safe_print(f"⚠️ Error processing {phone}: {e}")

async def master_polling_loop():
    safe_print("🚀 Master Polling Loop Started...")
    
    while True:
        try:
            # 1. Fetch Active Orders
            conn = sqlite3.connect("bot_database.db")
            active_orders = conn.cursor().execute("SELECT assigned_to, number, country_id FROM numbers WHERE status = 1").fetchall()
            countries = {row[0]: row[1] for row in conn.cursor().execute("SELECT id, name FROM countries").fetchall()}
            conn.close()

            if not active_orders:
                await asyncio.sleep(2)
                continue

            # DEBUG: Print active numbers to console to verify switch
            # This helps confirm if the bot is tracking the NEW number
            print(f"📡 Polling {len(active_orders)} numbers: {[x[1] for x in active_orders]}")

            # 2. Parallel Processing
            tasks = []
            for user_id, phone, c_id in active_orders:
                tasks.append(process_number_task(user_id, phone, c_id, countries))
            
            await asyncio.gather(*tasks)
            
            await asyncio.sleep(1)

        except Exception as e:
            safe_print(f"Loop Error: {e}")
            await asyncio.sleep(5)

# --- SERVER & MAIN ---
async def web_handler(request): return web.Response(text="Bot Running")
async def start_web_server():
    app = web.Application(); app.router.add_get('/', web_handler)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 8080))).start()

async def main():
    safe_print("System Starting...")
    asyncio.create_task(start_web_server())
    asyncio.create_task(master_polling_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
