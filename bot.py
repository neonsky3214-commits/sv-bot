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
B2B_KEYWORD = "b2b"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_direct_stats(date_from: str, date_to: str) -> dict:
    """Получаем расходы из Яндекс Директ по кампаниям"""
    url = "https://api.direct.yandex.com/json/v5/reports"
    headers = {
        "Authorization": f"Bearer {YANDEX_TOKEN}",
        "Client-Login": "",  # оставь пустым если работаешь со своим аккаунтом
        "Accept-Language": "ru",
        "processingMode": "auto",
        "returnMoneyInMicros": "false",
        "skipReportHeader": "true",
        "skipColumnHeader": "false",
        "skipReportSummary": "true",
    }
    body = {
        "params": {
            "SelectionCriteria": {
                "DateFrom": date_from,
                "DateTo": date_to,
            },
            "FieldNames": ["CampaignName", "Cost"],
            "ReportName": f"cost_report_{date_from}",
            "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
            "DateRangeType": "CUSTOM_DATE",
            "Format": "TSV",
            "IncludeVAT": "YES",
        }
    }

    resp = requests.post(url, json=body, headers=headers)
    if resp.status_code not in (200, 201, 202):
        logger.error(f"Директ API ошибка: {resp.status_code} {resp.text}")
        return {"b2b": 0, "other": 0}

    lines = resp.text.strip().split("\n")
    b2b_cost = 0.0
    other_cost = 0.0

    for line in lines[1:]:  # пропускаем заголовок
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        campaign_name = parts[0].lower()
        try:
            cost = float(parts[1])
        except ValueError:
            continue
        if B2B_KEYWORD.lower() in campaign_name:
            b2b_cost += cost
        else:
            other_cost += cost

    return {"b2b": b2b_cost, "other": other_cost}


def get_metrika_stats(date: str) -> dict:
    """Получаем конверсии и доход из Яндекс Метрики по источнику 'реклама'"""
    url = f"https://api-metrika.yandex.net/stat/v1/data"
    headers = {"Authorization": f"OAuth {YANDEX_TOKEN}"}

    params = {
        "ids": METRIKA_COUNTER,
        "metrics": "ym:s:visits,ym:s:goal1reaches,ym:s:goal1conversionRate",
        "dimensions": "ym:s:lastSignTrafficSource",
        "filters": "ym:s:lastSignTrafficSource=='ad'",
        "date1": date,
        "date2": date,
        "limit": 1,
    }

    resp = requests.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        logger.error(f"Метрика API ошибка: {resp.status_code} {resp.text}")
        return {"visits": 0, "conversions": 0}

    data = resp.json()
    totals = data.get("totals", [0, 0, 0])
    return {
        "visits": int(totals[0]) if len(totals) > 0 else 0,
        "conversions": int(totals[1]) if len(totals) > 1 else 0,
    }


def get_metrika_revenue(date: str) -> dict:
    """Получаем доход из Метрики ecommerce по источнику реклама, с разбивкой b2b/other по utm"""
    url = "https://api-metrika.yandex.net/stat/v1/data"
    headers = {"Authorization": f"OAuth {YANDEX_TOKEN}"}

    # Общий доход от рекламы
    params = {
        "ids": METRIKA_COUNTER,
        "metrics": "ym:s:purchaseRevenue,ym:s:purchases",
        "dimensions": "ym:s:lastSignUTMCampaign",
        "filters": "ym:s:lastSignTrafficSource=='ad'",
        "date1": date,
        "date2": date,
        "limit": 100,
    }

    resp = requests.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        logger.error(f"Метрика revenue ошибка: {resp.status_code} {resp.text}")
        return {"b2b_revenue": 0, "other_revenue": 0, "b2b_purchases": 0, "other_purchases": 0}

    data = resp.json()
    b2b_revenue = 0.0
    other_revenue = 0.0
    b2b_purchases = 0
    other_purchases = 0

    for row in data.get("data", []):
        campaign = (row["dimensions"][0].get("name") or "").lower()
        metrics = row.get("metrics", [0, 0])
        revenue = float(metrics[0]) if len(metrics) > 0 else 0
        purchases = int(metrics[1]) if len(metrics) > 1 else 0
        if B2B_KEYWORD in campaign:
            b2b_revenue += revenue
            b2b_purchases += purchases
        else:
            other_revenue += revenue
            other_purchases += purchases

    return {
        "b2b_revenue": b2b_revenue,
        "other_revenue": other_revenue,
        "b2b_purchases": b2b_purchases,
        "other_purchases": other_purchases,
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

    direct = get_direct_stats(yesterday, yesterday)
    metrika = get_metrika_stats(yesterday)
    revenue = get_metrika_revenue(yesterday)

    total_cost = direct["b2b"] + direct["other"]
    total_revenue = revenue["b2b_revenue"] + revenue["other_revenue"]
    total_purchases = revenue["b2b_purchases"] + revenue["other_purchases"]

    msg = f"""📊 *Отчёт за {date_label}*

━━━━━━━━━━━━━━━━
🏢 *B2B*
💸 Расходы: {format_money(direct['b2b'])}
💰 Доход: {format_money(revenue['b2b_revenue'])}
🛒 Заявки: {revenue['b2b_purchases']}
📈 ROAS: {calc_roas(revenue['b2b_revenue'], direct['b2b'])}

━━━━━━━━━━━━━━━━
⛵ *Остальные кампании*
💸 Расходы: {format_money(direct['other'])}
💰 Доход: {format_money(revenue['other_revenue'])}
🛒 Конверсии: {revenue['other_purchases']}
📈 ROAS: {calc_roas(revenue['other_revenue'], direct['other'])}

━━━━━━━━━━━━━━━━
📌 *Итого*
💸 Расходы: {format_money(total_cost)}
💰 Доход: {format_money(total_revenue)}
🛒 Всего конверсий: {total_purchases}
👥 Визиты с рекламы: {metrika['visits']}
📈 ROAS: {calc_roas(total_revenue, total_cost)}"""

    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    logger.info("Отчёт отправлен!")


async def main():
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    # Отправка каждый день в 10:00 МСК
    scheduler.add_job(send_daily_report, "cron", hour=10, minute=0)
    scheduler.start()
    logger.info("Бот запущен. Отчёты каждый день в 10:00 МСК")

    # Тестовая отправка сразу при запуске
    await send_daily_report()

    # Держим бота живым
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
