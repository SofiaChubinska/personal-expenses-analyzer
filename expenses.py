"""Import and analyze personal expenses without network dependencies."""
import csv
import io
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from categories import get_category

CENT = Decimal("0.01")
REQUIRED = {"date", "description", "amount", "currency"}


def read_transactions(data, excel=False):
    if excel:
        from openpyxl import load_workbook

        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            sheet = iter(workbook.active.values)
            header = list(next(sheet, ()))
            rows = (dict(zip(header, row)) for row in sheet)
            return validate_rows(header, rows)
        finally:
            workbook.close()

    source = io.StringIO(data.decode("utf-8-sig"), newline="")
    reader = csv.DictReader(source)
    return validate_rows(reader.fieldnames or [], reader)


def validate_rows(header, rows):
    if not REQUIRED.issubset(header) or len(set(header)) != len(header):
        raise ValueError("Потрібні унікальні колонки date, description, amount, currency.")
    transactions = []
    for line, row in enumerate(rows, 2):
        if not any(value is not None and value != "" for value in row.values()):
            continue
        if line > 20001:
            raise ValueError("Ліміт імпорту становить 20 000 рядків.")
        raw_date = row.get("date")
        description = str(row.get("description") or "").strip()
        currency = str(row.get("currency") or "").strip().upper()
        try:
            if isinstance(raw_date, datetime):
                day = raw_date.date()
            else:
                raw_date = str(raw_date)
                if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw_date):
                    raise ValueError
                day = date.fromisoformat(raw_date)
            amount = Decimal(str(row.get("amount")).strip().replace(",", "."))
            if not amount.is_finite() or not 0 <= amount <= 10**12 or amount != amount.quantize(CENT):
                raise ValueError
            if not description or not re.fullmatch(r"[A-Z]{3}", currency):
                raise ValueError
        except (ValueError, InvalidOperation):
            raise ValueError(f"Рядок {line}. Перевірте дату, опис, суму й код валюти.") from None
        category = str(row.get("category") or "").strip() or get_category(description)
        transactions.append((day, description, amount, currency, category))
    if not transactions:
        raise ValueError("Файл не містить операцій.")
    return transactions


def month_name(number):
    year, month = divmod(number, 12)
    return f"{year:04d}-{month + 1:02d}"


def money(amount):
    return str(amount.quantize(CENT))


def analyze(transactions, currency, exclude_latest=False):
    categories = defaultdict(Decimal)
    months = defaultdict(Decimal)
    daily = defaultdict(Decimal)
    selected = []
    for day, description, amount, code, category in transactions:
        if code != currency:
            continue
        selected.append((day.isoformat(), description, money(amount), category))
        categories[category] += amount
        months[day.year * 12 + day.month - 1] += amount
        daily[day.isoformat()] += amount
    if not selected:
        raise ValueError("Відсутні операції у вибраній валюті.")
    total = sum(categories.values(), Decimal(0))
    ranked = sorted(categories.items(), key=lambda item: (-item[1], item[0]))
    latest = max(months)
    history_end = latest - int(exclude_latest)
    window = list(range(history_end - 2, history_end + 1))
    observations = [months[key] for key in window] if all(key in months for key in window) else []
    forecast = money(sum(observations) / 3) if observations and history_end + 1 < 10000 * 12 else None

    errors = []
    for target in sorted(months):
        previous = range(target - 3, target)
        if target <= history_end and all(key in months for key in previous):
            baseline = sum(months[key] for key in previous) / 3
            errors.append(abs(months[target] - baseline))

    optional = sum((categories.get(name, Decimal(0)) for name in ("Кафе та ресторани", "Розваги")), Decimal(0))
    recommendations = []
    if optional > 0:
        recommendations.append(
            f"Кафе та розваги разом становлять {money(optional)} {currency}. "
            f"За сценарієм скорочення на 10% економія за цей період склала б {money(optional / 10)} {currency}."
        )
    if total > 0:
        recommendations.append(f"Найбільша категорія — {ranked[0][0]}. Перегляньте окремі операції в цій категорії.")
    return {
        "currency": currency,
        "total": money(total),
        "count": len(selected),
        "categories": [
            {"name": name, "amount": money(value), "share": float(value / total * 100) if total else 0}
            for name, value in ranked
        ],
        "daily": [[key, money(value)] for key, value in sorted(daily.items())],
        "monthly": [[month_name(key), money(value)] for key, value in sorted(months.items())],
        "transactions": sorted(selected, reverse=True),
        "forecast": forecast,
        "target_month": month_name(history_end + 1) if forecast is not None else None,
        "forecast_window": [month_name(key) for key in window] if forecast is not None else [],
        "mae": money(sum(errors) / len(errors)) if errors else None,
        "validation_count": len(errors),
        "recommendations": recommendations,
    }
