import asyncio
import os
import random
import re
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher
from aiogram.types import Message

# =====================================================================
# КОНФИГ — ВСЁ ИЗ ОКРУЖЕНИЯ (НА ХОСТИНГЕ)
# =====================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
print(f"[BOOT] Токен получен: {'ДА' if BOT_TOKEN else 'НЕТ'}, длина: {len(BOT_TOKEN)}", flush=True)

# Интервал публикации (сек) — рандом между MIN и MAX
# По умолчанию: от 10 до 15 минут (10*60 = 600 сек, 15*60 = 900 сек)
PUBLISH_INTERVAL_MIN = int(os.getenv("PUBLISH_INTERVAL_MIN", 10 * 60))
PUBLISH_INTERVAL_MAX = int(os.getenv("PUBLISH_INTERVAL_MAX", 15 * 60))

# Лимит Telegram на редактирование — 48ч. Берём 47ч с запасом.
EDIT_TTL_HOURS = int(os.getenv("EDIT_TTL_HOURS", 47))

# =====================================================================
# ПАРА «ИСТОЧНИК → ЦЕЛЬ» + ШАПКА (ОСТАВЛЕН ТОЛЬКО 1 КАНАЛ)
# =====================================================================

PAIRS = {
    os.getenv("SRC1", "").strip(): {
        "target": os.getenv("DST1", "").strip(),
        "header": "♠️ Масть 21 классик",
        "type": "suit21",
    },
}

# Если SRC1 не задан, бот не должен падать, но должен предупредить
if not PAIRS.get(""):
    pass # Это просто заглушка, чтобы не ругался линтер

# =====================================================================
# РЕГУЛЯРКА ПОД МАСТИ 21 КЛАССИК
# =====================================================================

# Формат: 🎯 Игра: #N437 ♠️
RE_SUIT21 = re.compile(
    r"🎯\s*Игра:\s*#N(\d+)\s*([♠♣♦♥])\ufe0f?",
    re.IGNORECASE,
)

# =====================================================================
# ФИЛЬТР / ПАРСИНГ
# =====================================================================

def extract_forecast_line(text: str, ftype: str):
    """
    Возвращает «чистую» строку прогноза (без ставки) или None.
    """
    if not text:
        return None

    if ftype == "suit21":
        m = RE_SUIT21.search(text)
        if not m:
            return None
        return f"🎯 Игра: #N{m.group(1)} {m.group(2)}"

    return None


def get_result_mark(text: str) -> str:
    """Возвращает '✅', '❌' или ''."""
    if not text:
        return ""
    if "✅" in text:
        return "✅"
    if "❌" in text:
        return "❌"
    return ""

# =====================================================================
# СОСТОЯНИЕ
# =====================================================================

# source_channel -> {
#   "text": чистый прогноз,
#   "msg_id": id в источнике,
#   "result": "✅"/"❌"/"",
#   "at": datetime
# }
last_forecast = {}

# (source_channel, source_msg_id) -> {
#   "target_channel": ...,
#   "target_msg_id": ...,
#   "published_at": datetime,
#   "result_at_publish": "✅"/"❌"/"",
# }
published = {}

# уже опубликованные (source_channel, source_msg_id) — чтобы не постить дважды
posted = set()

# =====================================================================
# БОТ
# =====================================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def channel_key(chat) -> str:
    if chat.username:
        return "@" + chat.username
    return str(chat.id)


# =====================================================================
# НОВЫЙ ПОСТ В ИСТОЧНИКЕ
# =====================================================================

@dp.channel_post()
async def on_channel_post(message: Message):
    key = channel_key(message.chat)

    if key not in PAIRS:
        return

    cfg = PAIRS[key]
    raw = message.text or message.caption or ""

    clean = extract_forecast_line(raw, cfg["type"])
    if not clean:
        return

    result = get_result_mark(raw)

    last_forecast[key] = {
        "text": clean,
        "msg_id": message.message_id,
        "result": result,
        "at": datetime.utcnow(),
    }

    print(
        f"[+] {key} ({cfg['type']}) msg={message.message_id} "
        f"result='{result}' → {clean}",
        flush=True
    )

# =====================================================================
# РЕДАКТИРОВАНИЕ ПОСТА В ИСТОЧНИКЕ (✅ / ❌)
# =====================================================================

@dp.edited_channel_post()
async def on_edited_channel_post(message: Message):
    key = channel_key(message.chat)

    if key not in PAIRS:
        return

    cfg = PAIRS[key]
    raw = message.text or message.caption or ""

    clean = extract_forecast_line(raw, cfg["type"])
    if not clean:
        return

    result = get_result_mark(raw)
    source_msg_id = message.message_id

    # 1) обновляем last_forecast, если это последний прогноз канала
    if key in last_forecast and last_forecast[key]["msg_id"] == source_msg_id:
        last_forecast[key]["text"] = clean
        last_forecast[key]["result"] = result
        last_forecast[key]["at"] = datetime.utcnow()

    # 2) если этот прогноз уже опубликован — редактируем сообщение в цели
    rec = published.get((key, source_msg_id))
    if not rec:
        return

    # TTL
    age = datetime.utcnow() - rec["published_at"]
    if age > timedelta(hours=EDIT_TTL_HOURS):
        print(f"[!] TTL истёк: {key} msg={source_msg_id}", flush=True)
        return

    # если результат не изменился — не дёргаем Telegram
    if rec["result_at_publish"] == result:
        return

    target_channel = rec["target_channel"]
    target_msg_id = rec["target_msg_id"]
    header = cfg["header"]

    new_text = f"{header}\n\n{clean}"
    if result:
        new_text += f" {result}"

    try:
        await bot.edit_message_text(
            chat_id=target_channel,
            message_id=target_msg_id,
            text=new_text,
        )
        rec["result_at_publish"] = result
        print(
            f"[✎] обновил {key} msg={source_msg_id} → "
            f"{target_channel} msg={target_msg_id} ({result or 'без результата'})",
            flush=True
        )
    except Exception as e:
        print(f"[x] ошибка редактирования: {e}", flush=True)

# =====================================================================
# ПУБЛИКАЦИЯ
# =====================================================================

async def publisher_loop():
    while True:
        delay = random.randint(PUBLISH_INTERVAL_MIN, PUBLISH_INTERVAL_MAX)
        print(f"[~] следующая публикация через {delay // 60} мин", flush=True)
        await asyncio.sleep(delay)

        # каналы, у которых есть прогноз и он ещё не опубликован
        available = [
            ch for ch, item in last_forecast.items()
            if (ch, item["msg_id"]) not in posted
        ]

        if not available:
            print("[!] нет свежих прогнозов — пропуск", flush=True)
            continue

        source_channel = random.choice(available)
        item = last_forecast[source_channel]
        cfg = PAIRS[source_channel]

        target_channel = cfg["target"]
        header = cfg["header"]

        text = f"{header}\n\n{item['text']}"
        if item["result"]:
            text += f" {item['result']}"

        try:
            sent = await bot.send_message(target_channel, text)
            posted.add((source_channel, item["msg_id"]))

            published[(source_channel, item["msg_id"])] = {
                "target_channel": target_channel,
                "target_msg_id": sent.message_id,
                "published_at": datetime.utcnow(),
                "result_at_publish": item["result"],
            }

            print(
                f"[✓] опубликовал {source_channel} → {target_channel} "
                f"msg={sent.message_id} ('{item['result'] or 'без результата'}')",
                flush=True
            )
        except Exception as e:
            print(f"[x] ошибка публикации: {e}", flush=True)

# =====================================================================
# ЗАПУСК
# =====================================================================

async def main():
    if not BOT_TOKEN:
        print("[x] BOT_TOKEN не задан", flush=True)
        return

    # Проверка, что SRC1 и DST1 заданы
    src = os.getenv("SRC1", "").strip()
    dst = os.getenv("DST1", "").strip()
    
    if not src or not dst:
        print("[FATAL] SRC1 или DST1 не заданы на хостинге!", flush=True)
        return

    print("[BOOT] Проверка переменных каналов:", flush=True)
    print(f"  {src} -> {dst}", flush=True)

    print("[BOOT] Инициализация бота...", flush=True)
    try:
        me = await bot.get_me()
        print(f"[BOOT] Бот авторизован: @{me.username}", flush=True)
        
        # ВАЖНО: Удаляем вебхук, чтобы polling работал!
        await bot.delete_webhook(drop_pending_updates=True)
        print("[BOOT] Вебхук удален, можно слушать polling", flush=True)
        
    except Exception as e:
        print(f"[FATAL] Ошибка: {e}", flush=True)
        return

    print("[✓] relay запущен", flush=True)
    asyncio.create_task(publisher_loop())
    
    print("[BOOT] Стартуем polling...", flush=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())