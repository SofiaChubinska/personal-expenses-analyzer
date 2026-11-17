CATEGORY_RULES = {
    "Продукти": ["сільпо", "атб", "novus", "продукт"],
    "Транспорт": ["uber", "bolt", "таксі", "метро", "пальне"],
    "Кафе та ресторани": ["кава", "кафе", "ресторан", "піца"],
    "Здоров’я": ["апте", "ліки", "ліків", "лікар", "аналіз"],
    "Комунальні послуги": [
        "інтернет", "електроенергі", "водопостачання", "опалення"
    ],
    "Розваги": ["кіно", "netflix", "spotify", "концерт"],
}


def get_category(description):
    text = description.casefold()

    for category, keywords in CATEGORY_RULES.items():
        if any(keyword in text for keyword in keywords):
            return category

    return "Інше"


