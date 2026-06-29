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
FORM_GOAL_ID = "277270181"      # цель "Заполнение форм Обратный звонок"
B2B_LANDING = "/corporate"      # страница входа для B2B-заявок
VAT_RATE = 1.22                 # коэффициент расхода с НДС (+22%)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

METRIKA_URL = "https://api-metrika.yandex.net/stat/v1/data"
HEADERS = {"Authorization": f"OAuth {YANDEX_TOKEN}"}


def is_b2b(campaign_name: str) -> bool:
    return B2B_KEYWORD in campaign_name.lower()


def metrika_totals(metrics: str, filters: str, date: str) -> list:
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
    """Заявки B2B — цель 'заполнение форм' по визитам с входом на /corporate"""
    t = metrika_totals(
        f"ym:s:goal{FORM_GOAL_ID}reaches",
        f"ym:s:lastSignTrafficSource=='ad' AND ym:s:startURL=@'{B2B_LANDING}'",
        date,
    )
    return int(t[0]) if t else 0


def get_retail(date: str) -> dict:
    """Розница: оплаты, доход, кол-во товаров по всей рекламе"""
    t = metrika_totals(
        "ym:s:ecommerceRevenue,ym:s:ecommercePurchases,ym:s:productPurchasedQuantity",
        "ym:s:lastSignTrafficSource=='ad'",
        date,
    )
    return {
        "revenue": float(t[0]) if len(t) > 0 else 0,
        "orders": int(t[1]) if len(t) > 1 else 0,
        "products": int(t[2]) if len(t) > 2 else 0,
    }


def get_top_products(date: str, top_n: int = 5) -> list:
    """Топ купленных товаров по рекламе (название, кол-во, сумма)"""
    params = {
        "ids": METRIKA_COUNTER,
        "metrics": "ym:s:productPurchasedQuantity,ym:s:productPurchasedPrice",
        "dimensions": "ym:s:productName",
        "filters": "ym:s:lastSignTrafficSource=='ad'",
        "date1": date,
        "date2": date,
        "limit": 100,
    }
    resp = requests.get(METRIKA_URL, headers=HEADERS, params=params)
    if resp.status_code != 200:
        logger.error(f"Метрика товары ошибка: {resp.status_code} {resp.text}")
        return []

    grouped = {}
    for row in resp.json().get("data", []):
        name = (row["dimensions"][0].get("name") or "Без названия").strip()
        m = row.get("metrics", [0, 0])
        qty = int(m[0]) if len(m) > 0 else 0
        price = float(m[1]) if len(m) > 1 else 0
        if name in grouped:
            grouped[name]["qty"] += qty
            grouped[name]["price"] += price
        else:
            grouped[name] = {"qty": qty, "price": price}

    items = [{"name": n, "qty": v["qty"], "price": v["price"]} for n, v in grouped.items()]
    items.sort(key=lambda x: x["qty"], reverse=True)
    return items[:top_n]


def format_money(val: float) -> str:
    return f"{val:,.0f} ₽".replace(",", " ")


def calc_roas(revenue: float, cost: float) -> str:
    if cost == 0:
        return "—"
    return f"{revenue / cost:.1f}x"


def calc_per_unit(cost: float, units: int) -> str:
    if units == 0:
        return "—"
    return format_money(cost / units)


async def send_daily_report():
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    date_label = (datetime.now() - timedelta(days=1)).strftime("%d.%m.%Y")

    logger.info(f"Формирую отчёт за {yesterday}...")

    costs = get_ad_costs(yesterday)
    b2b_leads = get_b2b_leads(yesterday)
    retail = get_retail(yesterday)
    top_products = get_top_products(yesterday)

    b2b_cost = costs["b2b_cost"]
    other_cost = costs["other_cost"]
    total_cost = b2b_cost + other_cost
    total_clicks = costs["b2b_clicks"] + costs["other_clicks"]

    # Блок топ товаров
    if top_products:
        products_lines = "\n".join(
            f"• {p['name']} — {p['qty']} шт ({format_money(p['price'])})"
            for p in top_products
        )
        products_block = f"\n\n━━━━━━━━━━━━━━━━\n🏆 *Топ товаров*\n{products_lines}"
    else:
        products_block = ""

    msg = f"""📊 *Отчёт за {date_label}*

━━━━━━━━━━━━━━━━
🏢 *B2B*
💸 Расходы: {format_money(b2b_cost)}
🧾 Расходы с НДС: {format_money(b2b_cost * VAT_RATE)}
🖱 Клики: {costs['b2b_clicks']}
📝 Заявки (форма): {b2b_leads}
💵 CPL: {calc_per_unit(b2b_cost, b2b_leads)}

━━━━━━━━━━━━━━━━
⛵ *Розница*
💸 Расходы: {format_money(other_cost)}
🧾 Расходы с НДС: {format_money(other_cost * VAT_RATE)}
🖱 Клики: {costs['other_clicks']}
🛒 Оплаты/заказы: {retail['orders']}
💵 CPO: {calc_per_unit(other_cost, retail['orders'])}
💰 Доход: {format_money(retail['revenue'])}
📈 ROAS: {calc_roas(retail['revenue'], other_cost)}{products_block}

━━━━━━━━━━━━━━━━
📌 *Итого*
💸 Расходы: {format_money(total_cost)}
🧾 Расходы с НДС: {format_money(total_cost * VAT_RATE)}
🖱 Клики: {total_clicks}
💰 Доход: {format_money(retail['revenue'])}"""

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
