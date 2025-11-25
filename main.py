import asyncio
import logging
import sqlite3
import aiohttp
import re
import os
import sys
from datetime import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatType, ChatMemberStatus
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest
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

# Group ID (OTP Forwarding)
GROUP_ID = -1003472422744

# =================================================

# লগিং
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
user_tasks = {}

# --- সেফ প্রিন্ট ---
def safe_print(text):
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode('utf-8', errors='ignore').decode('utf-8'))

# --- সেফ কলব্যাক ---
async def safe_answer(callback: types.CallbackQuery, text: str = None, alert: bool = False):
    try:
        if text: await callback.answer(text, show_alert=alert)
        else: await callback.answer()
    except: pass

# --- ডাটাবেস সেটআপ ---
def init_db():
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    
    # Countries Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS countries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        )
    """)
    
    # Numbers Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS numbers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_id INTEGER,
            number TEXT UNIQUE,
            status INTEGER DEFAULT 0
        )
    """)
    
    # Users Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY
        )
    """)

    # Channels Table (New)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT UNIQUE,
            invite_link TEXT
        )
    """)
    
    conn.commit()
    conn.close()

init_db()

# --- FSM স্টেটস ---
class AdminStates(StatesGroup):
    waiting_country_name = State()
    waiting_number_input = State()
    waiting_broadcast_msg = State()
    waiting_channel_id = State()   # For adding channel
    waiting_channel_link = State() # For adding channel link
    last_msg_id = State()

# --- চ্যানেল জয়েন চেকার (DYNAMIC) ---
async def check_subscription(user_id):
    if user_id in ADMIN_IDS: return True
    
    conn = sqlite3.connect("bot_database.db")
    channels = conn.cursor().execute("SELECT chat_id FROM channels").fetchall()
    conn.close()
    
    if not channels: return True # কোনো চ্যানেল সেট করা না থাকলে ফ্রি এক্সেস
    
    not_joined = False
    for ch in channels:
        chat_id = ch[0]
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status not in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                not_joined = True
                break
        except Exception as e:
            safe_print(f"Check Sub Error (Bot Admin?): {e}")
            # বট অ্যাডমিন না থাকলে ইগনোর করবে, যাতে ইউজার আটকে না যায়
            pass
            
    return not not_joined

# --- জয়েন রিকোয়েস্ট কিবোর্ড (DYNAMIC) ---
def get_join_keyboard():
    conn = sqlite3.connect("bot_database.db")
    channels = conn.cursor().execute("SELECT invite_link FROM channels").fetchall()
    conn.close()
    
    kb = []
    for i, ch in enumerate(channels):
        kb.append([InlineKeyboardButton(text=f"📢 Join Channel {i+1}", url=ch[0])])
    
    kb.append([InlineKeyboardButton(text="✅ VERIFY JOIN", callback_data="verify_join")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

# --- API ফাংশন ---
async def check_otp_api(phone_number):
    clean_number = ''.join(filter(str.isdigit, str(phone_number)))
    params = {'token': API_TOKEN, 'filternum': clean_number, 'records': 50}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124 Safari/537.36"}
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False), timeout=timeout) as session:
            async with session.get(API_URL, params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "success" and data.get("data"):
                        try:
                            return sorted(data["data"], key=lambda x: x['dt'], reverse=True)
                        except: return data["data"]
    except: pass
    return []

# --- অ্যাডমিন কিবোর্ড (আপডেটেড) ---
def get_admin_reply_keyboard():
    kb = [
        [KeyboardButton(text="ADD COUNTRY"), KeyboardButton(text="REMOVE COUNTRY")],
        [KeyboardButton(text="ADD NUMBER"), KeyboardButton(text="📢 BROADCAST")],
        [KeyboardButton(text="ADD CHANNEL"), KeyboardButton(text="REMOVE CHANNEL")] # New Buttons
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

# --- START & VERIFY HANDLERS ---

@dp.message(Command("start"), F.chat.type == ChatType.PRIVATE)
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    
    conn = sqlite3.connect("bot_database.db")
    try:
        conn.cursor().execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
    except: pass
    conn.close()

    if user_id in user_tasks:
        task = user_tasks[user_id]
        if not task.done(): task.cancel()
        del user_tasks[user_id]

    # সাবস্ক্রিপশন চেক
    if not await check_subscription(user_id):
        await message.answer("⚠️ **বট ব্যবহার করতে নিচের চ্যানেলগুলোতে জয়েন করুন:**", reply_markup=get_join_keyboard())
        return

    if user_id in ADMIN_IDS:
        await message.answer("👑 স্বাগতম অ্যাডমিন!", reply_markup=get_admin_reply_keyboard())
        kb = get_country_inline_keyboard()
        if kb.inline_keyboard: await message.answer("User View:", reply_markup=kb)
        else: await message.answer("⚠️ বর্তমানে কোনো দেশ নেই।")
    else:
        kb = get_country_inline_keyboard()
        if not kb.inline_keyboard: await message.answer("বর্তমানে কোনো সার্ভিস নেই।", reply_markup=ReplyKeyboardRemove())
        else: await message.answer("দেশ সিলেক্ট করুন:", reply_markup=kb)

@dp.callback_query(F.data == "verify_join")
async def verify_join_handler(callback: types.CallbackQuery, state: FSMContext):
    if await check_subscription(callback.from_user.id):
        await safe_answer(callback, text="✅ Verified!")
        await callback.message.delete()
        await cmd_start(callback.message, state)
    else:
        await safe_answer(callback, text="❌ আপনি জয়েন করেননি!", alert=True)

# --- USER FLOW ---

@dp.callback_query(F.data == "show_country_list")
async def show_country_list_handler(callback: types.CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    user_id = callback.from_user.id
    if user_id in user_tasks:
        user_tasks[user_id].cancel()
        del user_tasks[user_id]
    kb = get_country_inline_keyboard()
    if not kb.inline_keyboard: await callback.message.edit_text("সার্ভিস নেই।")
    else: await callback.message.edit_text("দেশ সিলেক্ট করুন:", reply_markup=kb)

@dp.callback_query(F.data == "cancel_op")
async def cancel_operation(callback: types.CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    user_id = callback.from_user.id
    if user_id in user_tasks:
        user_tasks[user_id].cancel()
        del user_tasks[user_id]
    await state.clear()
    await callback.message.delete()
    await cmd_start(callback.message, state)

@dp.callback_query(F.data == "back_home")
async def back_home(callback: types.CallbackQuery, state: FSMContext):
    await cancel_operation(callback, state)

# --- ADMIN ACTIONS (CHANNEL MANAGEMENT) ---

# 1. ADD CHANNEL
@dp.message(F.text == "ADD CHANNEL", F.from_user.id.in_(ADMIN_IDS), F.chat.type == ChatType.PRIVATE)
async def admin_add_channel_start(message: types.Message, state: FSMContext):
    msg = await message.answer(
        "চ্যানেলের Username বা ID দিন (যেমন: @mychannel বা -100...):\n\n⚠️ **নোট:** বটকে অবশ্যই ওই চ্যানেলে অ্যাডমিন বানাবেন।", 
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="back_home")]])
    )
    await state.update_data(last_msg_id=msg.message_id)
    await state.set_state(AdminStates.waiting_channel_id)

@dp.message(AdminStates.waiting_channel_id, F.from_user.id.in_(ADMIN_IDS))
async def admin_add_channel_id(message: types.Message, state: FSMContext):
    chat_id = message.text.strip()
    await state.update_data(chat_id=chat_id)
    
    data = await state.get_data()
    try: await bot.edit_message_text(chat_id=message.chat.id, message_id=data.get("last_msg_id"), text=f"ID: {chat_id}\nএবার Invite Link দিন:")
    except: await message.answer("এবার Invite Link দিন:")
    
    await message.delete()
    await state.set_state(AdminStates.waiting_channel_link)

@dp.message(AdminStates.waiting_channel_link, F.from_user.id.in_(ADMIN_IDS))
async def admin_add_channel_save(message: types.Message, state: FSMContext):
    link = message.text.strip()
    data = await state.get_data()
    chat_id = data.get("chat_id")
    
    conn = sqlite3.connect("bot_database.db")
    try:
        conn.cursor().execute("INSERT INTO channels (chat_id, invite_link) VALUES (?, ?)", (chat_id, link))
        conn.commit()
        res = f"✅ চ্যানেল অ্যাড হয়েছে!\nID: {chat_id}"
    except: res = "❌ এই চ্যানেলটি আগেই অ্যাড করা আছে।"
    conn.close()
    
    await message.delete()
    try: await bot.edit_message_text(chat_id=message.chat.id, message_id=data.get("last_msg_id"), text=res)
    except: await message.answer(res)
    await state.clear()

# 2. REMOVE CHANNEL
@dp.message(F.text == "REMOVE CHANNEL", F.from_user.id.in_(ADMIN_IDS), F.chat.type == ChatType.PRIVATE)
async def admin_rem_channel_start(message: types.Message):
    conn = sqlite3.connect("bot_database.db")
    channels = conn.cursor().execute("SELECT id, chat_id FROM channels").fetchall()
    conn.close()
    
    if not channels: await message.answer("কোনো চ্যানেল নেই!")
    else:
        btns = [[InlineKeyboardButton(text=f"❌ {ch[1]}", callback_data=f"del_ch_{ch[0]}")] for ch in channels]
        btns.append([InlineKeyboardButton(text="Cancel", callback_data="back_home")])
        await message.answer("চ্যানেল রিমুভ করুন:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("del_ch_"))
async def delete_channel_action(callback: types.CallbackQuery):
    await safe_answer(callback)
    if callback.from_user.id not in ADMIN_IDS: return
    
    ch_id = callback.data.split("_")[2]
    conn = sqlite3.connect("bot_database.db")
    conn.cursor().execute("DELETE FROM channels WHERE id = ?", (ch_id,))
    conn.commit()
    conn.close()
    await callback.message.edit_text("✅ চ্যানেল রিমুভ করা হয়েছে।")

# --- OTHER ADMIN ACTIONS ---

@dp.message(F.text == "📢 BROADCAST", F.from_user.id.in_(ADMIN_IDS), F.chat.type == ChatType.PRIVATE)
async def admin_broadcast_start(message: types.Message, state: FSMContext):
    msg = await message.answer("মেসেজ লিখুন:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="back_home")]]))
    await state.update_data(last_msg_id=msg.message_id)
    await state.set_state(AdminStates.waiting_broadcast_msg)

@dp.message(AdminStates.waiting_broadcast_msg, F.from_user.id.in_(ADMIN_IDS))
async def admin_broadcast_send(message: types.Message, state: FSMContext):
    text = message.text
    conn = sqlite3.connect("bot_database.db")
    users = conn.cursor().execute("SELECT user_id FROM users").fetchall()
    conn.close()
    cnt = 0
    sts = await message.answer("🚀 Sending...")
    for u in users:
        try:
            await bot.send_message(u[0], text)
            cnt += 1
            await asyncio.sleep(0.05)
        except: pass
    await sts.edit_text(f"✅ Sent to {cnt} users.")
    await state.clear()

@dp.message(F.text == "ADD COUNTRY", F.from_user.id.in_(ADMIN_IDS), F.chat.type == ChatType.PRIVATE)
async def admin_add_country_start(message: types.Message, state: FSMContext):
    msg = await message.answer("নাম লিখুন:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="back_home")]]))
    await state.update_data(last_msg_id=msg.message_id)
    await state.set_state(AdminStates.waiting_country_name)

@dp.message(AdminStates.waiting_country_name, F.from_user.id.in_(ADMIN_IDS))
async def save_country_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    data = await state.get_data()
    conn = sqlite3.connect("bot_database.db")
    try:
        conn.cursor().execute("INSERT INTO countries (name) VALUES (?)", (name,))
        conn.commit()
        res = f"✅ '{name}' Added."
    except: res = f"❌ Exists."
    conn.close()
    try: await message.delete()
    except: pass
    if data.get("last_msg_id"):
        try: await bot.edit_message_text(chat_id=message.chat.id, message_id=data.get("last_msg_id"), text=res)
        except: await message.answer(res)
    await state.clear()

@dp.message(F.text == "REMOVE COUNTRY", F.from_user.id.in_(ADMIN_IDS), F.chat.type == ChatType.PRIVATE)
async def admin_rem_country_start(message: types.Message):
    conn = sqlite3.connect("bot_database.db")
    countries = conn.cursor().execute("SELECT id, name FROM countries").fetchall()
    conn.close()
    if not countries: await message.answer("Empty!")
    else:
        btns = [[InlineKeyboardButton(text=f"❌ {c[1]}", callback_data=f"del_c_{c[0]}")] for c in countries]
        btns.append([InlineKeyboardButton(text="Cancel", callback_data="back_home")])
        await message.answer("Remove:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("del_c_"))
async def delete_country_action(callback: types.CallbackQuery):
    await safe_answer(callback)
    if callback.from_user.id not in ADMIN_IDS: return
    c_id = callback.data.split("_")[2]
    conn = sqlite3.connect("bot_database.db")
    conn.cursor().execute("DELETE FROM countries WHERE id = ?", (c_id,))
    conn.cursor().execute("DELETE FROM numbers WHERE country_id = ?", (c_id,))
    conn.commit()
    conn.close()
    await callback.message.edit_text("✅ Removed.")

@dp.message(F.text == "ADD NUMBER", F.from_user.id.in_(ADMIN_IDS), F.chat.type == ChatType.PRIVATE)
async def admin_add_number_start(message: types.Message):
    conn = sqlite3.connect("bot_database.db")
    countries = conn.cursor().execute("SELECT id, name FROM countries").fetchall()
    conn.close()
    if not countries: await message.answer("Add Country First!")
    else:
        btns = [[InlineKeyboardButton(text=c[1], callback_data=f"sel_cn_{c[0]}_{c[1]}")] for c in countries]
        btns.append([InlineKeyboardButton(text="Cancel", callback_data="back_home")])
        await message.answer("Select Country:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("sel_cn_"))
async def select_input_method(callback: types.CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    if callback.from_user.id not in ADMIN_IDS: return
    part = callback.data.split("_")
    await state.update_data(country_id=part[2], country_name=part[3])
    btns = [[InlineKeyboardButton(text="📂 File", callback_data="in_file")], [InlineKeyboardButton(text="✍️ Written", callback_data="in_text")], [InlineKeyboardButton(text="🔙 Cancel", callback_data="back_home")]]
    msg = await callback.message.edit_text(f"Selected: {part[3]}\nMethod:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
    await state.update_data(last_msg_id=msg.message_id)

@dp.callback_query(F.data.in_({"in_file", "in_text"}))
async def request_number_input(callback: types.CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    if callback.from_user.id not in ADMIN_IDS: return
    mode = callback.data
    await state.update_data(mode=mode)
    text = "Send .txt File" if mode == "in_file" else "Type Numbers:"
    msg = await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Cancel", callback_data="back_home")]]))
    await state.update_data(last_msg_id=msg.message_id)
    await state.set_state(AdminStates.waiting_number_input)

@dp.message(AdminStates.waiting_number_input, F.from_user.id.in_(ADMIN_IDS))
async def process_numbers(message: types.Message, state: FSMContext):
    data = await state.get_data()
    content = ""
    if data['mode'] == "in_file" and message.document:
        file = await bot.get_file(message.document.file_id)
        downloaded = await bot.download_file(file.file_path)
        content = downloaded.read().decode('utf-8')
    elif data['mode'] == "in_text" and message.text: content = message.text
    else: 
        try: await message.delete()
        except: pass
        return
    
    nums = [n.strip() for n in re.split(r'[,\n\r]+', content) if n.strip()]
    conn = sqlite3.connect("bot_database.db")
    added = 0
    for n in nums:
        try:
            conn.cursor().execute("INSERT INTO numbers (country_id, number, status) VALUES (?, ?, 0)", (data['country_id'], n))
            added += 1
        except:
            conn.cursor().execute("UPDATE numbers SET status = 0 WHERE number = ? AND country_id = ?", (n, data['country_id']))
            added += 1
    conn.commit()
    conn.close()
    try: await message.delete()
    except: pass
    res = f"✅ Added {added} numbers."
    if data.get("last_msg_id"):
        try: await bot.edit_message_text(chat_id=message.chat.id, message_id=data.get("last_msg_id"), text=res)
        except: await message.answer(res)
    await state.clear()

@dp.callback_query(F.data.startswith("buy_"))
async def user_buy_number(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    await safe_answer(callback)
    
    if not await check_subscription(user_id):
        await safe_answer(callback, text="Join Channels First!", alert=True)
        return

    if user_id in user_tasks:
        task = user_tasks[user_id]
        if not task.done():
            task.cancel()
            await asyncio.sleep(0.5)
        del user_tasks[user_id]
    
    part = callback.data.split("_")
    conn = sqlite3.connect("bot_database.db")
    res = conn.cursor().execute("SELECT number FROM numbers WHERE country_id = ? AND status = 0 LIMIT 1", (part[1],)).fetchone()
    
    if not res:
        conn.close()
        await safe_answer(callback, text="Stock Empty!", alert=True)
        return
    phone = res[0]
    conn.cursor().execute("UPDATE numbers SET status = 1 WHERE number = ?", (phone,))
    conn.commit()
    conn.close()
    
    text = f"🌎 {part[2]} WS Number Assigned:\n<code>+{phone}</code>\n\nWaiting for OTP..."
    kb = [[InlineKeyboardButton(text="CHANGE NUMBER", callback_data=f"buy_{part[1]}_{part[2]}")], [InlineKeyboardButton(text="CHANGE COUNTRY", callback_data="show_country_list")], [InlineKeyboardButton(text="CANCEL OPERATION", callback_data="cancel_op")]]
    sent_msg = await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    
    safe_print(f"Started: {phone}")
    user_tasks[user_id] = asyncio.create_task(otp_checker_task(bot, callback.message.chat.id, phone, part[2], sent_msg.message_id))

async def otp_checker_task(bot: Bot, chat_id: int, phone_number: str, country_name: str, message_id: int):
    last_dt = None
    for _ in range(120):
        try:
            await asyncio.sleep(5)
            msgs = await check_otp_api(phone_number)
            if msgs:
                latest = msgs[0]
                if last_dt is None or latest.get("dt") != last_dt:
                    last_dt = latest.get("dt")
                    msg_body = latest.get("message", "")
                    
                    svc = latest.get("cli", "Service")
                    svc = svc.capitalize() if svc and svc != "null" else "Unknown"
                    
                    # Universal Regex for 4-8 digits (Matches XXX-XXX, XXX XXX, XXXXXX)
                    otp_match = re.search(r'(?:\d{3}[-\s]\d{3})|(?<!\d)\d{4,8}(?!\d)', msg_body)
                    otp = otp_match.group(0) if otp_match else "N/A"
                    
                    ctime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    masked = f"{phone_number[:4]}***{phone_number[-4:]}" if len(phone_number) > 7 else phone_number
                    
                    utxt = f"🌎 Country : {country_name}\n🔢 Number : <code>{phone_number}</code>\n🔑 OTP : <code>{otp}</code>\n💸 Reward: 🔥"
                    gtxt = f"✅ {country_name} {svc} OTP Received!\n━━━━━━━━━━━━━━━━━━━━\n📱 Number: <code>{masked}</code>\n🌍 Country: {country_name}\n⚙️ Service: {svc}\n🔒 OTP Code: <code>{otp}</code>\n⏳ Time: {ctime}\n━━━━━━━━━━━━━━━━━━━━\nMessage:\n{msg_body}"
                    
                    safe_print(f"OTP: {otp}")
                    try: await bot.send_message(chat_id, utxt)
                    except: pass
                    try: await bot.send_message(GROUP_ID, gtxt)
                    except: pass
        except asyncio.CancelledError: break
        except: await asyncio.sleep(5)

async def web_handler(request): return web.Response(text="Bot is running!")
async def start_web_server():
    app = web.Application()
    app.router.add_get('/', web_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 8080))).start()

async def main():
    safe_print("Bot is running...")
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
