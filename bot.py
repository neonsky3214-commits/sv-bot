import logging
from datetime import datetime, timedelta
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
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


def metrika_totals(metrics: str, filters: str, date1: str, date2: str) -> list:
    params = {
        "ids": METRIKA_COUNTER,
        "metrics": metrics,
        "date1": date1,
        "date2": date2,
        "limit": 1,
    }
    if filters:
        params["filters"] = filters
    resp = requests.get(METRIKA_URL, headers=HEADERS, params=params)
    if resp.status_code != 200:
        logger.error(f"Метрика ошибка ({metrics}): {resp.status_code} {resp.text}")
        return [0] * len(metrics.split(","))
    return resp.json().get("totals", [0] * len(metrics.split(",")))


def get_ad_costs(date1: str, date2: str) -> dict:
    params = {
        "ids": METRIKA_COUNTER,
        "metrics": "ym:ad:RUBConvertedAdCost,ym:ad:clicks",
        "dimensions": "ym:ad:directOrder",
        "date1": date1,
        "date2": date2,
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


def get_b2b_leads(date1: str, date2: str) -> int:
    t = metrika_totals(
        f"ym:s:goal{FORM_GOAL_ID}reaches",
        f"ym:s:lastSignTrafficSource=='ad' AND ym:s:startURL=@'{B2B_LANDING}'",
        date1, date2,
    )
    return int(t[0]) if t else 0


def get_retail(date1: str, date2: str) -> dict:
    t = metrika_totals(
        "ym:s:ecommerceRevenue,ym:s:ecommercePurchases,ym:s:productPurchasedQuantity",
        "ym:s:lastSignTrafficSource=='ad'",
        date1, date2,
    )
    return {
        "revenue": float(t[0]) if len(t) > 0 else 0,
        "orders": int(t[1]) if len(t) > 1 else 0,
        "products": int(t[2]) if len(t) > 2 else 0,
    }


def get_top_products(date1: str, date2: str, top_n: int = 10) -> list:
    params = {
        "ids": METRIKA_COUNTER,
        "metrics": "ym:s:productPurchasedQuantity,ym:s:productPurchasedPrice",
        "dimensions": "ym:s:productName",
        "filters": "ym:s:lastSignTrafficSource=='ad'",
        "date1": date1,
        "date2": date2,
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
    items.sort(key=lambda x: x["price"], reverse=True)
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


def build_report(date1: str, date2: str) -> str:
    """Собирает текст отчёта за период [date1; date2]"""
    d1 = datetime.strptime(date1, "%Y-%m-%d").strftime("%d.%m.%Y")
    d2 = datetime.strptime(date2, "%Y-%m-%d").strftime("%d.%m.%Y")
    period_label = d1 if date1 == date2 else f"{d1} — {d2}"

    costs = get_ad_costs(date1, date2)
    b2b_leads = get_b2b_leads(date1, date2)
    retail = get_retail(date1, date2)
    top_products = get_top_products(date1, date2)

    b2b_cost = costs["b2b_cost"]
    other_cost = costs["other_cost"]
    total_cost = b2b_cost + other_cost
    total_clicks = costs["b2b_clicks"] + costs["other_clicks"]

    if top_products:
        products_lines = "\n".join(
            f"• {p['name']} — {format_money(p['price'])} ({p['qty']} шт)"
            for p in top_products
        )
        products_block = f"\n\n━━━━━━━━━━━━━━━━\n🏆 *Топ товаров*\n{products_lines}"
    else:
        products_block = ""

    return f"""📊 *Отчёт за {period_label}*

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


def valid_date(s: str) -> bool:
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


# === КОМАНДЫ ===

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 Привет! Я присылаю отчёт по рекламе.\n\n"
        "Команды:\n"
        "/today — отчёт за сегодня\n"
        "/yesterday — за вчера\n"
        "/date 2026-06-27 — за конкретный день\n"
        "/period 2026-06-01 2026-06-27 — за период\n\n"
        "Автоматически отчёт за вчера приходит каждый день в 10:00 МСК."
    )
    await update.message.reply_text(text)


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = datetime.now().strftime("%Y-%m-%d")
    await update.message.reply_text("Считаю отчёт за сегодня…")
    report = build_report(d, d)
    await update.message.reply_text(report, parse_mode="Markdown")


async def cmd_yesterday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    await update.message.reply_text("Считаю отчёт за вчера…")
    report = build_report(d, d)
    await update.message.reply_text(report, parse_mode="Markdown")


async def cmd_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not valid_date(context.args[0]):
        await update.message.reply_text("Укажи дату так: /date 2026-06-27")
        return
    d = context.args[0]
    await update.message.reply_text(f"Считаю отчёт за {d}…")
    report = build_report(d, d)
    await update.message.reply_text(report, parse_mode="Markdown")


async def cmd_period(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2 or not valid_date(context.args[0]) or not valid_date(context.args[1]):
        await update.message.reply_text("Укажи период так: /period 2026-06-01 2026-06-27")
        return
    d1, d2 = context.args[0], context.args[1]
    if d1 > d2:
        d1, d2 = d2, d1
    await update.message.reply_text(f"Считаю отчёт за {d1} — {d2}…")
    report = build_report(d1, d2)
    await update.message.reply_text(report, parse_mode="Markdown")


# === АВТОРАССЫЛКА ===

async def scheduled_report(app):
    d = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    report = build_report(d, d)
    await app.bot.send_message(chat_id=CHAT_ID, text=report, parse_mode="Markdown")
    logger.info("Авто-отчёт отправлен!")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("yesterday", cmd_yesterday))
    app.add_handler(CommandHandler("date", cmd_date))
    app.add_handler(CommandHandler("period", cmd_period))

    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(scheduled_report, "cron", hour=10, minute=0, args=[app])

    async def on_startup(application):
        scheduler.start()
        logger.info("Планировщик запущен. Авто-отчёт каждый день в 10:00 МСК")

    app.post_init = on_startup

    logger.info("Бот запущен.")
    app.run_polling()


if __name__ == "__main__":
    main()
