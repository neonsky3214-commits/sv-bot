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
FORM_GOAL_ID = "277270181"  # цель "Заполнение форм Обратный звонок"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

METRIKA_URL = "https://api-metrika.yandex.net/stat/v1/data"
HEADERS = {"Authorization": f"OAuth {YANDEX_TOKEN}"}


def is_b2b(campaign_name: str) -> bool:
    return B2B_KEYWORD in campaign_name.lower()


def metrika_totals(metrics: str, filters: str, date: str, extra: dict = None) -> list:
    """Универсальный запрос — возвращает массив totals"""
    params = {
        "ids": METRIKA_COUNTER,
        "metrics": metrics,
        "date1": date,
        "date2": date,
        "limit": 1,
    }
    if filters:
        params["filters"] = filters
    if extra:
        params.update(extra)
    resp = requests.get(METRIKA_URL, headers=HEADERS, params=params)
    if resp.status_code != 200:
        logger.error(f"Метрика ошибка ({metrics}): {resp.status_code} {resp.text}")
        return [0] * len(metrics.split(","))
    return resp.json().get("totals", [0] * len(metrics.split(",")))


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


def get_b2b_leads(date: str) -> int:
    """Заявки B2B — цель 'заполнение форм' по рекламным b2b-кампаниям"""
    t = metrika_totals(
        f"ym:s:goal{FORM_GOAL_ID}reaches",
        "ym:s:lastSignTrafficSource=='ad' AND ym:s:lastSignUTMCampaign=@'b2b'",
        date,
    )
    return int(t[0]) if t else 0


def get_revenue(date: str) -> dict:
    """Доход, заказы, купленные товары — всего по рекламе и отдельно B2B (для вычитания)"""
    # Всего по рекламе
    total = metrika_totals(
        "ym:s:ecommerceRevenue,ym:s:ecommercePurchases,ym:s:productPurchasedQuantity",
        "ym:s:lastSignTrafficSource=='ad'",
        date,
    )
    total_revenue = float(total[0]) if len(total) > 0 else 0
    total_orders = int(total[1]) if len(total) > 1 else 0
    total_products = int(total[2]) if len(total) > 2 else 0

    # B2B (чтобы вычесть из "остального")
    b2b = metrika_totals(
        "ym:s:ecommerceRevenue,ym:s:ecommercePurchases,ym:s:productPurchasedQuantity",
        "ym:s:lastSignTrafficSource=='ad' AND ym:s:lastSignUTMCampaign=@'b2b'",
        date,
    )
    b2b_revenue = float(b2b[0]) if len(b2b) > 0 else 0
    b2b_orders = int(b2b[1]) if len(b2b) > 1 else 0
    b2b_products = int(b2b[2]) if len(b2b) > 2 else 0

    return {
        "other_revenue": max(total_revenue - b2b_revenue, 0),
        "other_orders": max(total_orders - b2b_orders, 0),
        "other_products": max(total_products - b2b_products, 0),
    }


def format_money(val: float) -> str:
    return f"{val:,.0f} ₽".replace(",", " ")


def calc_roas(revenue: float, cost: float) -> str:
    if cost == 0:
        return "—"
    return f"{revenue / cost:.1f}x"


def calc_cpl(cost: float, leads: int) -> str:
    if leads == 0:
        return "—"
    return format_money(cost / leads)


async def send_daily_report():
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    date_label = (datetime.now() - timedelta(days=1)).strftime("%d.%m.%Y")

    logger.info(f"Формирую отчёт за {yesterday}...")

    costs = get_ad_costs(yesterday)
    b2b_leads = get_b2b_leads(yesterday)
    rev = get_revenue(yesterday)

    total_cost = costs["b2b_cost"] + costs["other_cost"]
    total_clicks = costs["b2b_clicks"] + costs["other_clicks"]

    msg = f"""📊 *Отчёт за {date_label}*

━━━━━━━━━━━━━━━━
🏢 *B2B*
💸 Расходы: {format_money(costs['b2b_cost'])}
🖱 Клики: {costs['b2b_clicks']}
📝 Заявки (форма): {b2b_leads}
💵 CPL: {calc_cpl(costs['b2b_cost'], b2b_leads)}

━━━━━━━━━━━━━━━━
⛵ *Остальные кампании*
💸 Расходы: {format_money(costs['other_cost'])}
🖱 Клики: {costs['other_clicks']}
💰 Доход: {format_money(rev['other_revenue'])}
🛒 Заказы: {rev['other_orders']}
📦 Куплено товаров: {rev['other_products']}
📈 ROAS: {calc_roas(rev['other_revenue'], costs['other_cost'])}

━━━━━━━━━━━━━━━━
📌 *Итого*
💸 Расходы: {format_money(total_cost)}
🖱 Клики: {total_clicks}
💰 Доход: {format_money(rev['other_revenue'])}"""

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
