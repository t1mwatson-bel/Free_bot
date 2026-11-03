import asyncio
import random
import re
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.types import Message

# =====================================================================
# КОНФИГ
# =====================================================================

BOT_TOKEN = "123456:ABCDEF..."

# Каналы-источники (username или id)
SOURCE_CHANNELS = [
    "@channel_bot1",
    "@channel_bot2",
    "@channel_bot3",
]

# Твой канал, куда публикуем
TARGET_CHANNEL = "@my_forecast_channel"

# Интервал публикации (сек)
PUBLISH_INTERVAL_MIN = 15 * 60
PUBLISH_INTERVAL_MAX = 30 * 60

# Не постить один и тот же прогноз дважды
DEDUP = True

# =====================================================================
# ФИЛЬТР
# =====================================================================

CARD_RE = re.compile(r"(10|[2-9AJQKA])(♠|♣|♦|♥)")
GAME_RE = re.compile(r"#N(\d+)")

def is_forecast(text: str) -> bool:
    return bool(text) and bool(GAME_RE.search(text)) and bool(CARD_RE.search(text))

# =====================================================================
# СОСТОЯНИЕ (в памяти)
# =====================================================================

# channel_username -> {"text": ..., "msg_id": ..., "at": ...}
last_forecast = {}

# уже опубликованные msg_id (для дедупа)
posted_ids = set()

# =====================================================================
# БОТ
# =====================================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def channel_key(chat) -> str:
    if chat.username:
        return "@" + chat.username
    return str(chat.id)


@dp.channel_post()
async def on_channel_post(message: Message):
    """Ловит все посты в каналах, где бот участник/админ."""
    key = channel_key(message.chat)

    if key not in SOURCE_CHANNELS:
        return

    text = message.text or message.caption or ""
    if not is_forecast(text):
        return

    last_forecast[key] = {
        "text": text,
        "msg_id": message.message_id,
        "at": datetime.utcnow().isoformat(),
    }

    print(f"[+] {key}: {text[:60]}...")


# =====================================================================
# ПУБЛИКАЦИЯ
# =====================================================================

async def publisher_loop():
    while True:
        delay = random.randint(PUBLISH_INTERVAL_MIN, PUBLISH_INTERVAL_MAX)
        print(f"[~] следующая публикация через {delay // 60} мин")
        await asyncio.sleep(delay)

        # берём каналы, где есть прогноз
        available = [
            ch for ch, item in last_forecast.items()
            if not (DEDUP and item["msg_id"] in posted_ids)
        ]

        if not available:
            print("[!] нет свежих прогнозов — пропуск")
            continue

        channel = random.choice(available)
        item = last_forecast[channel]

        header = f"📊 Прогноз из {channel}\n\n"
        text = header + item["text"]

        try:
            await bot.send_message(TARGET_CHANNEL, text)
            posted_ids.add(item["msg_id"])
            print(f"[✓] опубликовал из {channel}: {item['text'][:60]}...")
        except Exception as e:
            print(f"[x] ошибка публикации: {e}")


# =====================================================================
# ЗАПУСК
# =====================================================================

async def main():
    print("[✓] relay запущен")
    asyncio.create_task(publisher_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
