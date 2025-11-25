import asyncio
import logging
import sqlite3
import aiohttp
import re
import os
from datetime import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
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

# --- কনফিগারেশন ---
BOT_TOKEN = "8070506568:AAE6mUi2wcXMRTnZRwHUut66Nlu1NQC8Opo"
ADMIN_IDS = [8308179143, 5085250851]

# [span_0](start_span)API Settings[span_0](end_span)
API_TOKEN = "Rk5CRTSGcX9fh1WHeIVxYViVlEhaUmSDXG1Qe1dOc2ZykmZGiw=="
API_URL = "http://51.77.216.195/crapi/dgroup/viewstats"

# Group ID
GROUP_ID = -1003472422744

# লগিং
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
user_tasks = {}

# --- ডাটাবেস সেটআপ ---
def init_db():
    conn = sqlite3.connect("bot_database.db")
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
            status INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY
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
    last_msg_id = State()

# --- API চেক ফাংশন ---
async def check_otp_api(phone_number):
    clean_number = ''.join(filter(str.isdigit, str(phone_number)))
    params = {
        [span_1](start_span)"token": API_TOKEN, #[span_1](end_span)
        [span_2](start_span)"filternum": clean_number, #[span_2](end_span)
        [span_3](start_span)"records": 20 #[span_3](end_span)
    }
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            async with session.get(API_URL, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    [span_4](start_span)# Success check
                    if data.get("status") == "success" and data.get("data"):
                        return data["data"]
                else:
                    print(f"API Error Status: {resp.status}")
    except Exception as e:
        print(f"API Connection Error: {e}")
    return []

# --- কিবোর্ড ---
def get_admin_reply_keyboard():
    kb = [
        [KeyboardButton(text="ADD COUNTRY"), KeyboardButton(text="REMOVE COUNTRY")],
        [KeyboardButton(text="ADD NUMBER"), KeyboardButton(text="📢 BROADCAST")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_country_inline_keyboard():
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM countries")
    countries = cursor.fetchall()
    conn.close()
    buttons = []
    for c_id, c_name in countries:
        buttons.append([InlineKeyboardButton(text=c_name, callback_data=f"buy_{c_id}_{c_name}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- হ্যান্ডলারস ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
    except: pass
    conn.close()

    if user_id in user_tasks:
        user_tasks[user_id].cancel()
        del user_tasks[user_id]

    if user_id in ADMIN_IDS:
        await message.answer("👑 স্বাগতম অ্যাডমিন!", reply_markup=get_admin_reply_keyboard())
        kb = get_country_inline_keyboard()
        if kb.inline_keyboard: await message.answer("User Demo View:", reply_markup=kb)
        else: await message.answer("⚠️ বর্তমানে কোনো দেশ অ্যাড করা নেই।")
    else:
        kb = get_country_inline_keyboard()
        if not kb.inline_keyboard: await message.answer("বর্তমানে কোনো সার্ভিস নেই।", reply_markup=ReplyKeyboardRemove())
        else: await message.answer("স্বাগতম! নিচে দেওয়া দেশগুলো থেকে সিলেক্ট করুন:", reply_markup=kb)

@dp.callback_query(F.data == "show_country_list")
async def show_country_list_handler(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id in user_tasks:
        user_tasks[user_id].cancel()
        del user_tasks[user_id]
    kb = get_country_inline_keyboard()
    if not kb.inline_keyboard: await callback.message.edit_text("বর্তমানে কোনো সার্ভিস নেই।")
    else: await callback.message.edit_text("স্বাগতম! নিচে দেওয়া দেশগুলো থেকে সিলেক্ট করুন:", reply_markup=kb)

@dp.callback_query(F.data == "cancel_op")
async def cancel_operation(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id in user_tasks:
        user_tasks[user_id].cancel()
        del user_tasks[user_id]
    await state.clear()
    await callback.message.delete()
    if user_id not in ADMIN_IDS: await cmd_start(callback.message, state)
    else: await callback.answer("অপারেশন ক্যান্সেল করা হয়েছে।")

@dp.callback_query(F.data == "back_home")
async def back_home(callback: types.CallbackQuery, state: FSMContext):
    await cancel_operation(callback, state)

# --- ADMIN ACTIONS ---

@dp.message(F.text == "📢 BROADCAST", F.from_user.id.in_(ADMIN_IDS))
async def admin_broadcast_start(message: types.Message, state: FSMContext):
    msg = await message.answer("ব্রডকাস্ট মেসেজ লিখুন:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data="back_home")]]))
    await state.update_data(last_msg_id=msg.message_id)
    await state.set_state(AdminStates.waiting_broadcast_msg)

@dp.message(AdminStates.waiting_broadcast_msg, F.from_user.id.in_(ADMIN_IDS))
async def admin_broadcast_send(message: types.Message, state: FSMContext):
    text = message.text
    conn = sqlite3.connect("bot_database.db")
    users = conn.cursor().execute("SELECT user_id FROM users").fetchall()
    conn.close()
    count = 0
    sts = await message.answer("🚀 Sending...")
    for u in users:
        try:
            await bot.send_message(u[0], text)
            count += 1
            await asyncio.sleep(0.05)
        except: pass
    await sts.edit_text(f"✅ Sent to {count} users.")
    await state.clear()

@dp.message(F.text == "ADD COUNTRY", F.from_user.id.in_(ADMIN_IDS))
async def admin_add_country_start(message: types.Message, state: FSMContext):
    msg = await message.answer("Country নাম:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="back_home")]]))
    await state.update_data(last_msg_id=msg.message_id)
    await state.set_state(AdminStates.waiting_country_name)

@dp.message(AdminStates.waiting_country_name, F.from_user.id.in_(ADMIN_IDS))
async def save_country_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    data = await state.get_data()
    last_msg_id = data.get("last_msg_id")
    conn = sqlite3.connect("bot_database.db")
    try:
        conn.cursor().execute("INSERT INTO countries (name) VALUES (?)", (name,))
        conn.commit()
        res = f"✅ '{name}' Added."
    except: res = f"❌ '{name}' Exists."
    conn.close()
    try: await message.delete()
    except: pass
    if last_msg_id:
        try: await bot.edit_message_text(chat_id=message.chat.id, message_id=last_msg_id, text=res)
        except: await message.answer(res)
    await state.clear()

@dp.message(F.text == "REMOVE COUNTRY", F.from_user.id.in_(ADMIN_IDS))
async def admin_rem_country_start(message: types.Message):
    conn = sqlite3.connect("bot_database.db")
    countries = conn.cursor().execute("SELECT id, name FROM countries").fetchall()
    conn.close()
    if not countries: await message.answer("Empty!")
    else:
        buttons = [[InlineKeyboardButton(text=f"❌ {c[1]}", callback_data=f"del_c_{c[0]}")] for c in countries]
        buttons.append([InlineKeyboardButton(text="Cancel", callback_data="back_home")])
        await message.answer("Select to Remove:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("del_c_"))
async def delete_country_action(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    c_id = callback.data.split("_")[2]
    conn = sqlite3.connect("bot_database.db")
    conn.cursor().execute("DELETE FROM countries WHERE id = ?", (c_id,))
    conn.cursor().execute("DELETE FROM numbers WHERE country_id = ?", (c_id,))
    conn.commit()
    conn.close()
    await callback.message.edit_text("✅ Removed.")

@dp.message(F.text == "ADD NUMBER", F.from_user.id.in_(ADMIN_IDS))
async def admin_add_number_start(message: types.Message):
    conn = sqlite3.connect("bot_database.db")
    countries = conn.cursor().execute("SELECT id, name FROM countries").fetchall()
    conn.close()
    if not countries: await message.answer("Add Country First!")
    else:
        buttons = [[InlineKeyboardButton(text=c[1], callback_data=f"sel_cn_{c[0]}_{c[1]}")] for c in countries]
        buttons.append([InlineKeyboardButton(text="Cancel", callback_data="back_home")])
        await message.answer("Select Country:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("sel_cn_"))
async def select_input_method(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    part = callback.data.split("_")
    await state.update_data(country_id=part[2], country_name=part[3])
    buttons = [[InlineKeyboardButton(text="📂 File", callback_data="in_file")], [InlineKeyboardButton(text="✍️ Written", callback_data="in_text")], [InlineKeyboardButton(text="🔙 Cancel", callback_data="back_home")]]
    msg = await callback.message.edit_text(f"Selected: {part[3]}\nSelect Method:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.update_data(last_msg_id=msg.message_id)

@dp.callback_query(F.data.in_({"in_file", "in_text"}))
async def request_number_input(callback: types.CallbackQuery, state: FSMContext):
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
    c_id = data['country_id']
    mode = data['mode']
    last_msg_id = data.get("last_msg_id")
    content = ""
    if mode == "in_file" and message.document:
        file = await bot.get_file(message.document.file_id)
        downloaded = await bot.download_file(file.file_path)
        content = downloaded.read().decode('utf-8')
    elif mode == "in_text" and message.text: content = message.text
    else: 
        try: await message.delete()
        except: pass
        return
    
    raw_numbers = re.split(r'[,\n\r]+', content)
    # Filter only digits
    valid_numbers = [n.strip() for n in raw_numbers if n.strip().isdigit()]
    
    conn = sqlite3.connect("bot_database.db")
    added = 0
    for num in valid_numbers:
        try:
            conn.cursor().execute("INSERT INTO numbers (country_id, number, status) VALUES (?, ?, 0)", (c_id, num))
            added += 1
        except sqlite3.IntegrityError:
            conn.cursor().execute("UPDATE numbers SET status = 0 WHERE number = ? AND country_id = ?", (num, c_id))
            added += 1
    conn.commit()
    conn.close()
    try: await message.delete()
    except: pass
    res_text = f"✅ Added {added} numbers."
    if last_msg_id:
        try: await bot.edit_message_text(chat_id=message.chat.id, message_id=last_msg_id, text=res_text)
        except: await message.answer(res_text)
    await state.clear()

@dp.callback_query(F.data.startswith("buy_"))
async def user_buy_number(callback: types.CallbackQuery):
    part = callback.data.split("_")
    c_id, c_name = part[1], part[2]
    user_id = callback.from_user.id
    if user_id in user_tasks:
        user_tasks[user_id].cancel()
    
    conn = sqlite3.connect("bot_database.db")
    res = conn.cursor().execute("SELECT number FROM numbers WHERE country_id = ? AND status = 0 LIMIT 1", (c_id,)).fetchone()
    
    if not res:
        conn.close()
        await callback.answer("Stock Empty!", show_alert=True)
        return
    phone = res[0]
    conn.cursor().execute("UPDATE numbers SET status = 1 WHERE number = ?", (phone,))
    conn.commit()
    conn.close()
    
    text = f"🌎 {c_name} WS Number Assigned:\n<code>+{phone}</code>\n\nWaiting for OTP..."
    kb = [[InlineKeyboardButton(text="CHANGE NUMBER", callback_data=f"buy_{c_id}_{c_name}")], [InlineKeyboardButton(text="CHANGE COUNTRY", callback_data="show_country_list")], [InlineKeyboardButton(text="CANCEL OPERATION", callback_data="cancel_op")]]
    sent_msg = await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    
    print(f"Task Started: {phone}")
    user_tasks[user_id] = asyncio.create_task(otp_checker_task(bot, callback.message.chat.id, phone, c_name, sent_msg.message_id))

# --- ROBUST OTP CHECKER ---
async def otp_checker_task(bot: Bot, chat_id: int, phone_number: str, country_name: str, message_id: int):
    last_dt = None
    # লুপ যাতে ক্র্যাশ না করে তাই Try-Except লুপের ভেতরে দেওয়া হয়েছে
    for _ in range(120): # 10 minutes
        try:
            await asyncio.sleep(5)
            msgs = await check_otp_api(phone_number)
            
            if msgs:
                latest = msgs[0]
                if last_dt is None or latest.get("dt") != last_dt:
                    last_dt = latest.get("dt")
                    msg_body = latest.get("message", "")
                    
                    # 1. Service Detection[span_4](end_span)
                    service_name = latest.get("cli", "Service")
                    service_name = service_name.capitalize() if service_name and service_name != "null" else "Unknown"
                    
                    # 2. Advanced Regex for OTP (Includes XXX-XXX)
                    # This matches: 123-456 OR 123 456 OR 123456 (4-8 digits)
                    otp_match = re.search(r'(\d{3}[\s-]?\d{3})|(\b\d{4,8}\b)', msg_body)
                    otp = otp_match.group(0) if otp_match else "N/A"
                    
                    cur_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    masked = f"{phone_number[:4]}***{phone_number[-4:]}" if len(phone_number) > 7 else phone_number
                    
                    user_txt = f"🌎 Country : {country_name}\n🔢 Number : <code>{phone_number}</code>\n🔑 OTP : <code>{otp}</code>\n💸 Reward: 🔥"
                    group_txt = f"✅ {country_name} {service_name} OTP Received!\n━━━━━━━━━━━━━━━━━━━━\n📱 Number: <code>{masked}</code>\n🌍 Country: {country_name}\n⚙️ Service: {service_name}\n🔒 OTP Code: <code>{otp}</code>\n⏳ Time: {cur_time}\n━━━━━━━━━━━━━━━━━━━━\nMessage:\n{msg_body}"
                    
                    print(f"OTP Found: {otp} for {phone_number}")
                    
                    # Safe Send (User)
                    try: await bot.send_message(chat_id, user_txt)
                    except Exception as e: print(f"User Send Error: {e}")
                    
                    # Safe Send (Group)
                    try: await bot.send_message(GROUP_ID, group_txt)
                    except Exception as e: print(f"Group Send Error: {e}")

        except asyncio.CancelledError:
            break # Task Cancelled
        except Exception as e:
            print(f"Loop Error (Retrying): {e}")
            await asyncio.sleep(5) # Wait before retry

# --- WEB SERVER ---
async def web_handler(request): return web.Response(text="Bot Running")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', web_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

async def main():
    print("Bot Started...")
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
