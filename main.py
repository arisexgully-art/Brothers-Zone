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
import json
from datetime import datetime, timedelta
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
from bs4 import BeautifulSoup 

# ================= CONFIGURATION =================

BOT_TOKEN = "8070506568:AAE6mUi2wcXMRTnZRwHUut66Nlu1NQC8Opo"
ADMIN_IDS = [8308179143, 5085250851]

# Panel Credentials
PANEL_USER = "Momin11"
PANEL_PASS = "Ebrahim7258"
PANEL_URL = "http://139.99.63.204/ints" 

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

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect("bot_database.db")
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

# ================= PANEL SCRAPER ENGINE (VERIFIED) =================

class PanelSession:
    def __init__(self):
        self.session = None
        self.last_login = 0
        # Headers matched to your Android Logs
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 15; 23076PC4BI Build/AQ3A.240912.001) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.7444.102 Mobile Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "X-Requested-With": "mark.via.gp"
        }
    
    async def get_session(self):
        # Refresh session every 10 minutes
        if self.session and (time.time() - self.last_login < 600) and not self.session.closed:
            return self.session
        
        if self.session:
            await self.session.close()
        
        # Unsafe cookie jar to handle panel cookies better
        jar = aiohttp.CookieJar(unsafe=True)
        self.session = aiohttp.ClientSession(cookie_jar=jar, headers=self.headers)
        
        if await self.login():
            return self.session
        return None

    async def login(self):
        try:
            login_url = f"{PANEL_URL}/login"
            
            # 1. GET Login Page
            async with self.session.get(login_url) as resp:
                html_content = await resp.text()
            
            # 2. Captcha Solving
            math_match = re.search(r'(\d+)\s*([\+\-\*])\s*(\d+)\s*=', html_content)
            captcha_val = 0
            
            if math_match:
                n1, op, n2 = math_match.groups()
                if op == '+': captcha_val = int(n1) + int(n2)
                elif op == '-': captcha_val = int(n1) - int(n2)
                elif op == '*': captcha_val = int(n1) * int(n2)
                safe_print(f"🔐 Solved Captcha: {n1} {op} {n2} = {captcha_val}")
            else:
                # Fallback
                soup = BeautifulSoup(html_content, 'html.parser')
                label = soup.find('label', {'for': 'capt'})
                if label:
                    text = label.get_text()
                    m = re.search(r'(\d+)\s*\+\s*(\d+)', text)
                    if m:
                        captcha_val = int(m.group(1)) + int(m.group(2))
                        safe_print(f"🔐 Solved Captcha (BS4): {captcha_val}")

            await asyncio.sleep(1)

            # 3. POST Login
            payload = {
                'username': PANEL_USER,
                'password': PANEL_PASS,
                'capt': str(captcha_val)
            }
            
            post_headers = self.headers.copy()
            post_headers["Content-Type"] = "application/x-www-form-urlencoded"
            post_headers["Origin"] = "http://139.99.63.204"
            post_headers["Referer"] = login_url
            
            async with self.session.post(f"{PANEL_URL}/signin", data=payload, headers=post_headers, allow_redirects=False) as post_resp:
                
                # 302 Redirect = Success
                if post_resp.status == 302:
                    location = post_resp.headers.get('Location', '')
                    # Accept both absolute and relative paths
                    if "./" in location or "agent" in location or "index" in location:
                        self.last_login = time.time()
                        safe_print(f"✅ Panel Login Successful (Redirected to {location})")
                        return True
                
                # 200 OK = Maybe Success (if dashboard loaded directly)
                elif post_resp.status == 200:
                    content = await post_resp.text()
                    if "Logout" in content or "Welcome" in content:
                        self.last_login = time.time()
                        safe_print("✅ Panel Login Successful (Direct)")
                        return True
                    else:
                        soup = BeautifulSoup(content, 'html.parser')
                        err_div = soup.find("div", class_="alert-error") or soup.find("div", class_="alert")
                        err_msg = err_div.get_text().strip() if err_div else "Unknown Error"
                        safe_print(f"❌ Login Failed. Reason: {err_msg}")
                        return False
                
                return False

        except Exception as e:
            safe_print(f"❌ Login Exception: {e}")
            return False

panel_session = PanelSession()

async def fetch_panel_data(phone_number):
    try:
        session = await panel_session.get_session()
        if not session: return []

        # Date Range: Today + Yesterday (To fix timezone issues)
        now = datetime.now()
        yesterday = now - timedelta(days=2)
        
        fdate1 = f"{yesterday.strftime('%Y-%m-%d')} 00:00:00"
        fdate2 = f"{now.strftime('%Y-%m-%d')} 23:59:59"
        
        # Headers for AJAX Request
        ajax_headers = {
            "User-Agent": panel_session.headers["User-Agent"],
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest", # Required for JSON
            "Referer": f"{PANEL_URL}/agent/SMSCDRStats"
        }
        
        params = {
            'fdate1': fdate1,
            'fdate2': fdate2,
            'fnum': phone_number,
            'sEcho': '1',
            'iDisplayLength': '50',
            'iDisplayStart': '0',
            'iColumns': '9',
            'sColumns': ',,,,,,,,',
            'bRegex': 'false',
            'sSearch': ''
        }
        
        url = f"{PANEL_URL}/agent/res/data_smscdr.php"
        
        async with session.get(url, params=params, headers=ajax_headers) as resp:
            content_text = await resp.text()
            
            # Check for HTML (Session Expired)
            if "Login" in content_text or "<html" in content_text:
                safe_print("⚠️ Session expired (HTML received). Relogging...")
                panel_session.last_login = 0
                return []

            try:
                data = json.loads(content_text)
                messages = []
                if 'aaData' in data and data['aaData']:
                    for row in data['aaData']:
                        # Validating row length
                        if len(row) > 5:
                            # Checking phone match
                            if phone_number in str(row[2]):
                                messages.append({
                                    'dt': row[0],
                                    'message': row[5], 
                                    'cli': row[3]
                                })
                return messages
            except json.JSONDecodeError:
                pass
                
    except Exception as e:
        safe_print(f"⚠️ Scraper Error: {e}")
        panel_session.last_login = 0 
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
        # Only count available numbers
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
    
    # Clear active session
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
    
    try:
        await callback.message.edit_text(f"🔄 <b>Finding fresh number for {c_name}...</b>", reply_markup=None)
    except: pass

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    
    # Release previous number
    cursor.execute("DELETE FROM numbers WHERE assigned_to = ?", (user_id,))
    conn.commit()
    
    assigned_phone = None
    
    # Find new random number
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
    msg = await m.answer("Channel ID:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="back_home")]]))
    await state.update_data(last_msg_id=msg.message_id)
    await state.set_state(AdminStates.waiting_channel_id)

@dp.message(AdminStates.waiting_channel_id)
async def ach_id(m: types.Message, state: FSMContext):
    await state.update_data(chat_id=m.text.strip())
    d = await state.get_data()
    await m.delete()
    try: await bot.edit_message_text(chat_id=m.chat.id, message_id=d['last_msg_id'], text="Link:")
    except: pass
    await state.set_state(AdminStates.waiting_channel_link)

@dp.message(AdminStates.waiting_channel_link)
async def ach_save(m: types.Message, state: FSMContext):
    d = await state.get_data()
    conn = sqlite3.connect("bot_database.db")
    conn.cursor().execute("INSERT INTO channels (chat_id, invite_link) VALUES (?, ?)", (d['chat_id'], m.text.strip()))
    conn.commit()
    conn.close()
    await m.delete()
    try: await bot.edit_message_text(chat_id=m.chat.id, message_id=d['last_msg_id'], text="✅ Added")
    except: pass
    await state.clear()

@dp.message(F.text == "REMOVE CHANNEL", F.from_user.id.in_(ADMIN_IDS))
async def rch(m: types.Message):
    conn = sqlite3.connect("bot_database.db")
    chs = conn.cursor().execute("SELECT id, chat_id FROM channels").fetchall()
    conn.close()
    btns = [[InlineKeyboardButton(text=f"❌ {c[1]}", callback_data=f"del_ch_{c[0]}")] for c in chs]
    btns.append([InlineKeyboardButton(text="Cancel", callback_data="back_home")])
    await m.answer("Remove:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("del_ch_"))
async def dch(c: types.CallbackQuery):
    conn = sqlite3.connect("bot_database.db")
    conn.cursor().execute("DELETE FROM channels WHERE id=?", (c.data.split("_")[2],))
    conn.commit()
    conn.close()
    await c.message.edit_text("✅ Removed.")

@dp.message(F.text == "ADD COUNTRY", F.from_user.id.in_(ADMIN_IDS))
async def ac(m: types.Message, state: FSMContext):
    await state.clear()
    msg = await m.answer("Country Name:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="back_home")]]))
    await state.update_data(last_msg_id=msg.message_id)
    await state.set_state(AdminStates.waiting_country_name)

@dp.message(AdminStates.waiting_country_name)
async def ac_s(m: types.Message, state: FSMContext):
    conn = sqlite3.connect("bot_database.db")
    conn.cursor().execute("INSERT INTO countries (name) VALUES (?)", (m.text.strip(),))
    conn.commit()
    conn.close()
    await m.delete()
    d = await state.get_data()
    try: await bot.edit_message_text(chat_id=m.chat.id, message_id=d['last_msg_id'], text="✅ Added")
    except: pass
    await state.clear()

@dp.message(F.text == "REMOVE COUNTRY", F.from_user.id.in_(ADMIN_IDS))
async def rc(m: types.Message):
    conn = sqlite3.connect("bot_database.db")
    cs = conn.cursor().execute("SELECT id, name FROM countries").fetchall()
    conn.close()
    btns = [[InlineKeyboardButton(text=f"❌ {c[1]}", callback_data=f"del_c_{c[0]}")] for c in cs]
    btns.append([InlineKeyboardButton(text="Cancel", callback_data="back_home")])
    await m.answer("Remove:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("del_c_"))
async def rca(c: types.CallbackQuery):
    cid = c.data.split("_")[2]
    conn = sqlite3.connect("bot_database.db")
    conn.cursor().execute("DELETE FROM countries WHERE id=?", (cid,))
    conn.cursor().execute("DELETE FROM numbers WHERE country_id=?", (cid,))
    conn.commit()
    conn.close()
    await c.message.edit_text("✅ Removed")

@dp.message(F.text == "ADD NUMBER", F.from_user.id.in_(ADMIN_IDS))
async def an(m: types.Message, state: FSMContext):
    await state.clear()
    conn = sqlite3.connect("bot_database.db")
    cs = conn.cursor().execute("SELECT id, name FROM countries").fetchall()
    conn.close()
    btns = [[InlineKeyboardButton(text=c[1], callback_data=f"sel_cn_{c[0]}_{c[1]}")] for c in cs]
    btns.append([InlineKeyboardButton(text="Cancel", callback_data="back_home")])
    await m.answer("Select:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("sel_cn_"))
async def ans(c: types.CallbackQuery, state: FSMContext):
    p = c.data.split("_")
    await state.update_data(country_id=p[2], country_name=p[3])
    msg = await c.message.edit_text(f"Sel: {p[3]}\nMethod:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📂 File", callback_data="in_file")], [InlineKeyboardButton(text="✍️ Written", callback_data="in_text")]]))
    await state.update_data(last_msg_id=msg.message_id)

@dp.callback_query(F.data.in_({"in_file", "in_text"}))
async def ani(c: types.CallbackQuery, state: FSMContext):
    await state.update_data(mode=c.data)
    msg = await c.message.edit_text("Input:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="back_home")]]))
    await state.update_data(last_msg_id=msg.message_id)
    await state.set_state(AdminStates.waiting_number_input)

@dp.message(AdminStates.waiting_number_input)
async def anp(m: types.Message, state: FSMContext):
    d = await state.get_data()
    c = ""
    if d['mode'] == "in_file" and m.document: 
        f = await bot.get_file(m.document.file_id)
        c = (await bot.download_file(f.file_path)).read().decode('utf-8')
    elif d['mode'] == "in_text": 
        c = m.text
    
    nums = [n.strip() for n in re.split(r'[,\n\r\s]+', c) if n.strip().isdigit()]
    conn = sqlite3.connect("bot_database.db")
    a = 0
    for n in nums: 
        conn.cursor().execute("INSERT OR REPLACE INTO numbers (country_id, number, status, assigned_to) VALUES (?, ?, 0, NULL)", (d['country_id'], n))
        a += 1
    conn.commit()
    conn.close()
    await m.delete()
    try: await bot.edit_message_text(chat_id=m.chat.id, message_id=d['last_msg_id'], text=f"✅ Added {a}")
    except: pass
    await state.clear()

@dp.message(F.text == "📢 BROADCAST", F.from_user.id.in_(ADMIN_IDS))
async def bcs(m: types.Message, state: FSMContext):
    await state.clear()
    msg = await m.answer("Msg:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="back_home")]]))
    await state.update_data(last_msg_id=msg.message_id)
    await state.set_state(AdminStates.waiting_broadcast_msg)

@dp.message(AdminStates.waiting_broadcast_msg)
async def bcd(m: types.Message, state: FSMContext):
    conn = sqlite3.connect("bot_database.db")
    us = conn.cursor().execute("SELECT user_id FROM users").fetchall()
    conn.close()
    c = 0
    for u in us: 
        try: 
            await bot.copy_message(u[0], m.chat.id, m.message_id)
            c += 1
            await asyncio.sleep(0.05)
        except: pass
    await m.answer(f"Sent {c}")
    await state.clear()

# ================= POLLING LOGIC =================

async def process_number_task(user_id, phone, c_id, countries):
    try:
        msgs = await fetch_panel_data(phone)
        
        if not msgs: return

        for msg in sorted(msgs, key=lambda x: x['dt']):
            msg_body = msg.get("message", "")
            msg_dt = msg.get("dt", "")
            
            if not msg_body: continue

            # Robust Signature (Time + Body + Phone)
            sig_raw = f"{msg_dt}{msg_body}{phone}"
            sig = hashlib.md5(sig_raw.encode()).hexdigest()
            
            conn = sqlite3.connect("bot_database.db")
            cursor = conn.cursor()
            exists = cursor.execute("SELECT 1 FROM processed_sms WHERE signature = ?", (sig,)).fetchone()
            
            if not exists:
                cursor.execute("INSERT INTO processed_sms (signature) VALUES (?)", (sig,))
                conn.commit()
                
                country_name = countries.get(c_id, "Unknown")
                svc = msg.get("cli", "WhatsApp")
                
                otp_match = re.search(r'(?:\d{3}[-\s]\d{3})|(?<!\d)\d{4,8}(?!\d)', msg_body)
                otp = otp_match.group(0) if otp_match else "N/A"
                
                safe_msg = html.escape(msg_body)
                masked = f"{phone[:4]}***{phone[-4:]}" if len(phone) > 7 else phone
                
                utxt = f"🌎 Country : {country_name}\n🔢 Number : <code>{phone}</code>\n🔑 OTP : <code>{otp}</code>\n💸 Reward: 🔥"
                gtxt = f"✅ {country_name} {svc} OTP Received!\n━━━━━━━━━━━━━━━━━━━━\n📱 Number: <code>{masked}</code>\n🌍 Country: {country_name}\n⚙️ Service: {svc}\n🔒 OTP Code: <code>{otp}</code>\n⏳ Time: {msg_dt}\n━━━━━━━━━━━━━━━━━━━━\nMessage:\n{safe_msg}"
                
                safe_print(f"✅ FOUND OTP for {user_id}: {otp}")
                
                try: await bot.send_message(user_id, utxt)
                except: pass
                try: await bot.send_message(GROUP_ID, gtxt)
                except: pass
            
            conn.close()

    except Exception as e:
        safe_print(f"⚠️ Error processing {phone}: {e}")

async def master_polling_loop():
    safe_print("🚀 Panel Scraper Polling Started...")
    
    while True:
        try:
            conn = sqlite3.connect("bot_database.db")
            active_orders = conn.cursor().execute("SELECT assigned_to, number, country_id FROM numbers WHERE status = 1").fetchall()
            countries = {row[0]: row[1] for row in conn.cursor().execute("SELECT id, name FROM countries").fetchall()}
            conn.close()

            if not active_orders:
                await asyncio.sleep(2)
                continue
            
            # safe_print(f"📡 Tracking {len(active_orders)} numbers via Panel...")

            tasks = []
            for user_id, phone, c_id in active_orders:
                tasks.append(process_number_task(user_id, phone, c_id, countries))
            
            await asyncio.gather(*tasks)
            await asyncio.sleep(2)

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
