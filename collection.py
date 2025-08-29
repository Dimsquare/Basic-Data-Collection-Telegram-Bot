import asyncio
from os import getenv
import os
import random
from dotenv import load_dotenv
load_dotenv()

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, StateFilter
from aiogram.types import Message
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

import librosa
import numpy as np
import matplotlib.pyplot as plt
import tempfile, requests
from pathlib import Path

import aiosqlite
import bcrypt
import re
from datetime import datetime

from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext


TOKEN = os.getenv("TOKEN")


class Signup(StatesGroup):
    details = State()
    username = State()
    password = State()

class Login(StatesGroup):
    username = State()
    password = State()


dp = Dispatcher()


main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Sign Up"), KeyboardButton(text="Log In")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Choose an option…",
)

# After login: normal working keyboard
logged_in_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Record Audio")],
        [KeyboardButton(text="Me"), KeyboardButton(text="Log Out")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Select an action…",
)

# Ask if the user is ready to record
ready_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Yes"), KeyboardButton(text="No")],
        [KeyboardButton(text="Sign Up"), KeyboardButton(text="Log In")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Ready to record?",
)

# After each submission: continue or stop
record_action_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Continue"), KeyboardButton(text="Stop")],
        [KeyboardButton(text="Me"), KeyboardButton(text="Log Out")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Record another?",
)

# -------------------------------------------------------------------
# Random text prompts for recording
# -------------------------------------------------------------------
PROMPTS = [
    "The quick brown fox jumps over the lazy dog.",
    "Please record this sentence clearly in a quiet environment.",
    "Artificial intelligence is transforming the world.",
    "Good data collection requires consistent recording conditions.",
    "Librosa helps analyze audio signals in Python.",
    "This is a short sample to evaluate background noise levels.",
    "Speak at a natural pace and pronounce each word distinctly.",
    "Today is a great day to train a machine learning model.",
    "Clean audio leads to better recognition accuracy.",
    "Read this line as clearly as you can for the dataset."
]
def generate_prompt() -> str:
    return random.choice(PROMPTS)

# -------------------------------------------------------------------
# Database
# -------------------------------------------------------------------
DB_PATH = "users.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # users
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER,
                first_name TEXT,
                last_name TEXT,
                age TEXT,
                language TEXT,
                username TEXT UNIQUE,
                password_hash TEXT,
                audio_left INTEGER DEFAULT 10,
                created_at TEXT
            )
        """)
        # sessions (with current_prompt column)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                tg_id INTEGER PRIMARY KEY,
                username TEXT,
                logged_in_at TEXT
            )
        """)
        # Try to add current_prompt column if not present (ignore errors if it exists)
        try:
            await db.execute("ALTER TABLE sessions ADD COLUMN current_prompt TEXT")
        except Exception:
            pass

        # submissions log
        await db.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER,
                username TEXT,
                prompt TEXT,
                file_id TEXT,
                noise_level REAL,
                accepted INTEGER,
                created_at TEXT
            )
        """)
        await db.commit()

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def check_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

# -------------------------------------------------------------------
# Handlers
# -------------------------------------------------------------------
@dp.message(Command("start"))
async def command_start_handler(message: Message) -> None:
    # Always clear session on /start
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM sessions WHERE tg_id = ?", (message.from_user.id,))
        await db.commit()

    welcome = "Welcome to Dubem's Data Collection Bot"
    task = (
        "Tasks to complete:\n"
        "1) Read the text prompt out loud.\n"
        "2) Record and send your audio.\n"
        "3) Repeat to collect more samples.\n\n"
        "👉 Please log in to continue."
    )

    await message.answer(f"{welcome}\n\n{task}", reply_markup=main_kb)

@dp.message(Command("info"))
async def info_handler(message: Message) -> None:
    await message.answer("This is a simple Telegram bot using aiogram.", reply_markup=main_kb)

@dp.message(Command("status"))
async def status_handler(message: Message) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT audio_left FROM users WHERE tg_id = ?", (message.from_user.id,))
        row = await cur.fetchone()
    if row:
        await message.answer(f"Number of tasks left is: {row[0]}", reply_markup=logged_in_kb)
    else:
        await message.answer("None.", reply_markup=main_kb)

# Media handlers
@dp.message(F.photo)
async def image_handler(message: Message) -> None:
    await message.answer("Image submitted successfully!")

# Ask for an audio upload and give a prompt
@dp.message(StateFilter('*'), F.text == "Record Audio")
async def btn_upload_audio(message: Message, state: FSMContext):
    await state.clear()
    # Ensure logged in
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT username FROM sessions WHERE tg_id = ?", (message.from_user.id,))
        row = await cur.fetchone()
    if not row:
        return await message.answer("Please log in first.", reply_markup=ready_kb)

    prompt = generate_prompt()
    # Save current prompt in session
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE sessions SET current_prompt = ? WHERE tg_id = ?", (prompt, message.from_user.id))
        await db.commit()

    await message.answer(
        "🎙️ New recording prompt:\n\n"
        f"👉 {prompt}\n\n"
        "When ready, please send your audio file (as an audio message or file)."
    )

# Process incoming audio
@dp.message(F.voice | F.audio | F.document)
async def audio_handler(message: Message) -> None:
    if message.audio:
        file_id = message.audio.file_id
    elif message.voice:
        file_id = message.voice.file_id
    elif message.document:
        file_id = message.document.file_id
    else:
        return await message.answer("❌ Unsupported file type. Please send an audio or voice message.")

    # Look up username + current prompt
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT username, current_prompt FROM sessions WHERE tg_id = ?", (message.from_user.id,))
        sess = await cur.fetchone()
    username = sess[0] if sess else None
    current_prompt = sess[1] if (sess and len(sess) > 1) else None

    # Download file
    file = await message.bot.get_file(file_id)
    file_path = file.file_path
    file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"

    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=Path(file_path).suffix or ".mp3")
    r = requests.get(file_url)
    tmp_file.write(r.content)
    tmp_file.flush()

    # Load audio locally
    audio_data, sr = librosa.load(tmp_file.name, sr=None)

    # Compute RMS and noise
    rms = librosa.feature.rms(y=audio_data)[0]
    frames = range(len(rms))
    times = librosa.frames_to_time(frames, sr=sr)

    # (No plot is sent; keep compute consistent)
    plt.plot(times, rms)
    plt.xlabel("Time (s)")
    plt.ylabel("RMS Energy")
    plt.title("Signal Energy Over Time")
    plt.close()

    def detect_background_noise(y, sr, threshold=0.01):
        r = librosa.feature.rms(y=y)[0]
        noise_floor = np.percentile(r, 10)
        return noise_floor > threshold, noise_floor

    is_noisy, noise_level = detect_background_noise(audio_data, sr)

    # Log submission
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO submissions (tg_id, username, prompt, file_id, noise_level, accepted, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                message.from_user.id,
                username,
                current_prompt,
                file_id,
                float(noise_level),
                0 if is_noisy else 1,
                datetime.utcnow().isoformat(timespec="seconds"),
            ),
        )
        # Clear current prompt for next round
        await db.execute("UPDATE sessions SET current_prompt = NULL WHERE tg_id = ?", (message.from_user.id,))
        await db.commit()

    # Respond to user + decrement quota if accepted
    if is_noisy:
        await message.answer(
            "❌ Error \n"
            "Message: File not accepted \n"
            "Reason: High background noise detected \n"
            f"Noise Level: {noise_level:.5f} \n"
            "Resolution: Please record in a quieter environment and try again. The threshold is set at 0.01."
        )
    else:
        await message.answer("✅ Success \nAudio file submitted.")
        async with aiosqlite.connect(DB_PATH) as db:
            temp = await db.execute("SELECT audio_left FROM users WHERE tg_id = ?", (message.from_user.id,))
            row = await temp.fetchone()
            if row and row[0] > 0:
                new_count = row[0] - 1
                await db.execute("UPDATE users SET audio_left = ? WHERE tg_id = ?", (new_count, message.from_user.id))
                await db.commit()
                await message.answer(f"Audio files left: {new_count}", reply_markup=logged_in_kb)
            else:
                await message.answer("You have no audio submissions left. Please contact support.", reply_markup=logged_in_kb)

    # Ask to continue or stop after any result
    await message.answer("Continue?", reply_markup=record_action_kb)

# -------------------- Auth: Signup --------------------
@dp.message(Command("signup"))
async def signup_start(message: Message, state: FSMContext):
    await state.set_state(Signup.details)
    await message.answer(
        "Welcome to the signup process! Please provide the following details in the format:\n"
        "First Name, Last Name, Age, Language (e.g., John, Doe, 25, English):"
    )

@dp.message(Signup.details)
async def signup_details(message: Message, state: FSMContext):
    details = message.text.strip().split(",")

    if len(details) != 4:
        return await message.answer("Invalid format. Please provide details in the format:\nFirst Name, Last Name, Age, Language")
    
    firstname, lastname, age, language = [d.strip() for d in details]
    await state.set_state(Signup.username)
    await state.update_data(first_name=firstname, last_name=lastname, age=age, language=language)
    await message.answer("Choose a username (3–32 chars, letters/numbers/_):")

@dp.message(Signup.username)
async def signup_username(message: Message, state: FSMContext):
    username = message.text.strip()
    if not re.match(r"^[A-Za-z0-9_]{3,32}$", username):
        return await message.answer("Invalid username. Try again (3–32 chars, letters/numbers/_):")
    await state.update_data(username=username)
    await state.set_state(Signup.password)
    await message.answer("Choose a password (min 6 chars):")

@dp.message(Signup.password)
async def signup_password(message: Message, state: FSMContext):
    pwd = message.text.strip()
    if len(pwd) < 6:
        return await message.answer("Password too short. Enter at least 6 characters:")
    data = await state.get_data()
    username = data["username"]
    firstname = data.get("first_name")
    lastname = data.get("last_name")
    age = data.get("age")
    language = data.get("language")
    pwd_hash = hash_password(pwd)
    await message.answer("Password accepted!")

    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO users (tg_id, first_name, last_name, age, language, username, password_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (message.from_user.id, firstname, lastname, age, language, username, pwd_hash, datetime.utcnow().isoformat(timespec="seconds")),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            await state.clear()
            return await message.answer("⚠️ Username already taken. Start over with /signup.")

    await state.clear()
    await message.answer("✅ Account created! You can now /login.")

# -------------------- Auth: Login --------------------
@dp.message(Command("login"))
async def login_start(message: Message, state: FSMContext):
    await state.set_state(Login.username)
    await message.answer("Enter your username:")

@dp.message(Login.username)
async def login_username(message: Message, state: FSMContext):
    await state.update_data(username=message.text.strip())
    await state.set_state(Login.password)
    await message.answer("Enter your password:")

@dp.message(Login.password)
async def login_password(message: Message, state: FSMContext):
    data = await state.get_data()
    username = data["username"]
    password = message.text.strip()

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT password_hash FROM users WHERE username = ?", (username,))
        row = await cur.fetchone()

    if row and check_password(password, row[0]):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR REPLACE INTO sessions (tg_id, username, logged_in_at, current_prompt) VALUES (?, ?, ?, NULL)",
                (message.from_user.id, username, datetime.utcnow().isoformat(timespec="seconds")),
            )
            await db.commit()
        await message.answer(f"✅ Logged in as {username}")

        task = (
            "Tasks to complete:\n"
            "1) Read the text prompt out loud.\n"
            "2) Record and send your audio.\n"
            "3) Repeat to collect more samples.\n\n"
            "Ready to record? Choose Yes or No."
        )
        await message.answer(task, reply_markup=ready_kb)
    else:
        await message.answer("❌ Invalid username or password.", reply_markup=main_kb)

    await state.clear()

# -------------------- Auth: Logout / Me --------------------
@dp.message(Command("logout"))
async def logout_handler(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM sessions WHERE tg_id = ?", (message.from_user.id,))
        await db.commit()
    await message.answer("🚪 Logged out.", reply_markup=main_kb)

@dp.message(Command("me"))
async def me_handler(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT username, logged_in_at FROM sessions WHERE tg_id = ?",
            (message.from_user.id,),
        )
        row = await cur.fetchone()

    if row:
        await message.answer(f"👤 You are logged in as {row[0]}\n⏱️ Since: {row[1]}")
    else:
        await message.answer("⚠️ Not logged in. Use /login.")

# -------------------- Button shortcuts (work from ANY state) --------------------
@dp.message(StateFilter('*'), F.text == "Sign Up")
async def btn_signup(message: Message, state: FSMContext):
    await state.clear()
    await signup_start(message, state)

@dp.message(StateFilter('*'), F.text == "Log In")
async def btn_login(message: Message, state: FSMContext):
    await state.clear()
    await login_start(message, state)

@dp.message(StateFilter('*'), F.text == "Log Out")
async def btn_logout(message: Message, state: FSMContext):
    await state.clear()
    await logout_handler(message)

@dp.message(StateFilter('*'), F.text == "Me")
async def btn_me(message: Message, state: FSMContext):
    await state.clear()
    await me_handler(message)

# Ready to record? -> Yes
@dp.message(StateFilter('*'), F.text == "Yes")
async def ready_yes(message: Message, state: FSMContext):
    await state.clear()
    # Ensure user logged in
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT username FROM sessions WHERE tg_id = ?", (message.from_user.id,))
        sess = await cur.fetchone()
    if not sess:
        return await message.answer("You need to log in first. Tap Log In or Sign Up below.", reply_markup=ready_kb)

    prompt = generate_prompt()
    # Save prompt for this user
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE sessions SET current_prompt = ? WHERE tg_id = ?", (prompt, message.from_user.id))
        await db.commit()

    await message.answer(
        "🎙️ Please record the following:\n\n"
        f"👉 {prompt}\n\n"
        "Send your audio when ready.",
        reply_markup=logged_in_kb
    )

# Ready to record? -> No
@dp.message(StateFilter('*'), F.text == "No")
async def ready_no(message: Message, state: FSMContext):
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM sessions WHERE tg_id = ?", (message.from_user.id,))
        logged_in = await cur.fetchone()
    kb = logged_in_kb if logged_in else main_kb
    await message.answer("No problem — start anytime from the buttons below.", reply_markup=kb)

# After a submission -> Continue
@dp.message(StateFilter('*'), F.text == "Continue")
async def act_continue(message: Message, state: FSMContext):
    await state.clear()
    # Ensure user is logged in
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM sessions WHERE tg_id = ?", (message.from_user.id,))
        logged_in = await cur.fetchone()
    if not logged_in:
        return await message.answer("Please log in first.", reply_markup=ready_kb)

    prompt = generate_prompt()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE sessions SET current_prompt = ? WHERE tg_id = ?", (prompt, message.from_user.id))
        await db.commit()

    await message.answer(
        "🎙️ New recording prompt:\n\n"
        f"👉 {prompt}\n\n"
        "Send your audio when ready.",
        reply_markup=logged_in_kb
    )

# After a submission -> Stop
@dp.message(StateFilter('*'), F.text == "Stop")
async def act_stop(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("✅ Thanks for contributing! You can start again anytime.", reply_markup=logged_in_kb)

# -------------------------------------------------------------------
# Run the bot
# -------------------------------------------------------------------
async def main() -> None:
    await init_db()
    bot = Bot(token=TOKEN)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())