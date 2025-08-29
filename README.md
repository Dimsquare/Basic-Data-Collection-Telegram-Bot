# Basic-Data-Collection-Telegram-Bot

A Telegram bot to collect clean audio samples.  
- Users **sign up / log in** with username + password  
- Bot gives **random prompts** to read aloud  
- Accepts **voice notes or audio files**  
- Runs a **noise check** before saving  
- Stores everything in **SQLite**  

## Run
1. Install deps:  
   ```bash
   pip install aiogram aiosqlite bcrypt librosa numpy matplotlib python-dotenv requests soundfile

2. Add bot token to .env:
   TOKEN = 7863241607:AAFpeaOyJXLC2THHpHXrNcQEinMUFG5_Itc

3. Start the bot:
    python -m collection
