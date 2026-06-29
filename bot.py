import asyncio
import logging
from datetime import datetime, timedelta
import requests
from telegram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# === НАСТРОЙКИ ===
TELEGRAM_TOKEN = "8956060541:AAFwhquYW4h83YVD8a9t7GyWUmsDaJM4yyc"
CHAT_ID = 520032441
YANDEX_TOKEN = "y0__wgBEJ3Q8pgHGKXLRCDTxpmMGGSA4BK2B7UUkS6GUa74x3echsBM"
METRIKA_COUNTER = "45738897"
DIRECT_LOGIN = "yd-sv-rub-hl-463642-d81o"
B2B_KEYWORD = "b2b"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

METRIKA_URL = "https://api-metrika.yandex.net/stat/v1/data"
HEADERS = {"Authorization": f"OAuth {YANDEX_TOKEN}"}


def is_b2b(campaign_name: str) -> bool:
    return B2B_KEYWORD in campaign_name.lower()


def get_ad_costs(date: str) -> dict:
    """Расходы и клики из Метрики (данные Директа), разбивка b2b/other по имени кампании"""
    params = {
        "ids": METRIKA_COUNTER,
        "metrics": "ym:ad:RUBConvertedAdCost,ym:ad:clicks",
        "dimensions": "ym:ad:directOrder",
        "date1": date,
        "date2": date,
        "direct_client_logins": DIRECT_LOGIN,
        "limit": 200,
    }
    resp = requests.get(METRIKA_URL, headers=HEADERS, params=params)
    if resp.status_code != 200:
        logger.error(f"Метрика расходы ошибка: {resp.status_code} {resp.text}")
        return {"b2b_cost": 0, "other_cost": 0, "b2b_clicks": 0, "other_clicks": 0}

    data = resp.json()
    b2b_cost = other_cost = 0.0
    b2b_clicks = other_clicks = 0

    for row in data.get("data", []):
        name = row["dimensions"][0].get("name") or ""
        m = row.get("metrics", [0, 0])
        cost = float(m[0]) if len(m) > 0 else 0
        clicks = int(m[1]) if len(m) > 1 else 0
        if is_b2b(name):
            b2b_cost += cost
            b2b_clicks += clicks
        else:
            other_cost += cost
            other_clicks += clicks

    return {
        "b2b_cost": b2b_cost,
        "other_cost": other_cost,
        "b2b_clicks": b2b_clicks,
        "other_clicks": other_clicks,
    }


def get_ad_revenue(date: str) -> dict:
    """Доход и покупки из Метрики по рекламным кампаниям, разбивка b2b/other по имени кампании"""
    params = {
        "ids": METRIKA_COUNTER,
        "metrics": "ym:s:ecommerceRevenue,ym:s:ecommercePurchases,ym:s:visits",
        "dimensions": "ym:s:directOrder",
        "date1": date,
        "date2": date,
        "direct_client_logins": DIRECT_LOGIN,
        "limit": 200,
    }
    resp = requests.get(METRIKA_URL, headers=HEADERS, params=params)
    if resp.status_code != 200:
        logger.error(f"Метрика доход ошибка: {resp.status_code} {resp.text}")
        return {"b2b_revenue": 0, "other_revenue": 0, "b2b_purchases": 0,
                "other_purchases": 0, "b2b_visits": 0, "other_visits": 0}

    data = resp.json()
    b2b_revenue = other_revenue = 0.0
    b2b_purchases = other_purchases = 0
    b2b_visits = other_visits = 0

    for row in data.get("data", []):
        name = row["dimensions"][0].get("name") or ""
        m = row.get("metrics", [0, 0, 0])
        revenue = float(m[0]) if len(m) > 0 else 0
        purchases = int(m[1]) if len(m) > 1 else 0
        visits = int(m[2]) if len(m) > 2 else 0
        if is_b2b(name):
            b2b_revenue += revenue
            b2b_purchases += purchases
            b2b_visits += visits
        else:
            other_revenue += revenue
            other_purchases += purchases
            other_visits += visits

    return {
        "b2b_revenue": b2b_revenue,
        "other_revenue": other_revenue,
        "b2b_purchases": b2b_purchases,
        "other_purchases": other_purchases,
        "b2b_visits": b2b_visits,
        "other_visits": other_visits,
    }


def format_money(val: float) -> str:
    return f"{val:,.0f} ₽".replace(",", " ")


def calc_roas(revenue: float, cost: float) -> str:
    if cost == 0:
        return "—"
    return f"{revenue / cost:.1f}x"


async def send_daily_report():
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    date_label = (datetime.now() - timedelta(days=1)).strftime("%d.%m.%Y")

    logger.info(f"Формирую отчёт за {yesterday}...")

    costs = get_ad_costs(yesterday)
    rev = get_ad_revenue(yesterday)

    total_cost = costs["b2b_cost"] + costs["other_cost"]
    total_revenue = rev["b2b_revenue"] + rev["other_revenue"]
    total_purchases = rev["b2b_purchases"] + rev["other_purchases"]
    total_clicks = costs["b2b_clicks"] + costs["other_clicks"]

    msg = f"""📊 *Отчёт за {date_label}*

━━━━━━━━━━━━━━━━
🏢 *B2B*
💸 Расходы: {format_money(costs['b2b_cost'])}
💰 Доход: {format_money(rev['b2b_revenue'])}
🛒 Заявки: {rev['b2b_purchases']}
🖱 Клики: {costs['b2b_clicks']}
📈 ROAS: {calc_roas(rev['b2b_revenue'], costs['b2b_cost'])}

━━━━━━━━━━━━━━━━
⛵ *Остальные кампании*
💸 Расходы: {format_money(costs['other_cost'])}
💰 Доход: {format_money(rev['other_revenue'])}
🛒 Заявки: {rev['other_purchases']}
🖱 Клики: {costs['other_clicks']}
📈 ROAS: {calc_roas(rev['other_revenue'], costs['other_cost'])}

━━━━━━━━━━━━━━━━
📌 *Итого*
💸 Расходы: {format_money(total_cost)}
💰 Доход: {format_money(total_revenue)}
🛒 Всего заявок: {total_purchases}
🖱 Всего кликов: {total_clicks}
📈 ROAS: {calc_roas(total_revenue, total_cost)}"""

    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    logger.info("Отчёт отправлен!")


async def main():
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(send_daily_report, "cron", hour=10, minute=0)
    scheduler.start()
    logger.info("Бот запущен. Отчёты каждый день в 10:00 МСК")

    # Тестовая отправка сразу при запуске
    await send_daily_report()

    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
