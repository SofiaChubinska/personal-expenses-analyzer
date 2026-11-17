import argparse
import csv
import hashlib
import io
import json
import math
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

import requests
from dotenv import load_dotenv
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from pymongo import MongoClient
from pymongo.operations import SearchIndexModel

from categories import get_category

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
RAW_DIR = BASE_DIR / "data" / "raw"

MODEL_NAME = (
    "sentence-transformers/"
    "paraphrase-multilingual-MiniLM-L12-v2"
)
VECTOR_SIZE = 384
VECTOR_INDEX = "expenses_vector_index"

def get_model():
    return HuggingFaceEmbeddings(
        model_name=MODEL_NAME,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )


def save_original(source, extension=None):
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    if source.startswith(("https://", "http://")):
        suffix = extension or Path(urlsplit(source).path).suffix.lower()

        if suffix not in {".csv", ".txt", ".pdf"}:
            raise ValueError("Вкажіть --ext .csv, .txt або .pdf")

        response = requests.get(source, timeout=(10, 120))
        response.raise_for_status()
        content = response.content
    else:
        source_path = Path(source)
        suffix = source_path.suffix.lower()

        if suffix not in {".csv", ".txt", ".pdf"}:
            raise ValueError("Підтримуються CSV, TXT і PDF")

        content = source_path.read_bytes()

    document_id = hashlib.sha256(content).hexdigest()
    saved_path = RAW_DIR / f"{document_id}{suffix}"
    saved_path.write_bytes(content)

    return document_id, saved_path


def extract_content(path):
    if path.suffix == ".pdf":
        reader = PdfReader(path)
        text = "\n\n".join(
            page.extract_text() or ""
            for page in reader.pages
        )

        if not text.strip():
            raise ValueError(
                "PDF не містить текстового шару. "
                "Для сканованого документа потрібен OCR."
            )

        return text, []

    text = path.read_text(encoding="utf-8-sig")

    if path.suffix == ".txt":
        return text, []

    reader = csv.DictReader(io.StringIO(text))
    required = {"date", "description", "amount", "currency"}

    if not required.issubset(reader.fieldnames or []):
        raise ValueError(
            "CSV повинен містити колонки: "
            "date,description,amount,currency"
        )

    transactions = []

    for row in reader:
        datetime.strptime(row["date"], "%Y-%m-%d")
        amount = Decimal(row["amount"])

        if not amount.is_finite() or amount < 0:
            raise ValueError(
                "amount має бути невід’ємною скінченною сумою витрати"
            )

        row["category"] = get_category(row["description"])
        transactions.append(row)

    return text, transactions


def create_indexes(db):
    db.documents.create_index("sha256", unique=True)

    db.chunks.create_index(
        [("document_id", 1), ("position", 1)],
        unique=True,
    )

    existing_names = {
        index["name"]
        for index in db.chunks.list_search_indexes()
    }

    if VECTOR_INDEX not in existing_names:
        index = SearchIndexModel(
            name=VECTOR_INDEX,
            type="vectorSearch",
            definition={
                "fields": [
                    {
                        "type": "vector",
                        "path": "embedding",
                        "numDimensions": VECTOR_SIZE,
                        "similarity": "cosine",
                    }
                ]
            },
        )

        db.chunks.create_search_index(model=index)

    print(
        "Звичайні індекси створено. "
        "Векторний індекс створюється асинхронно."
    )


def ingest(db, source, extension=None):
    document_id, path = save_original(source, extension)

    existing = db.documents.find_one({"_id": document_id})

    if existing and existing.get("status") == "ready":
        print("Цей документ уже оброблено:", document_id)
        return

    full_text, transactions = extract_content(path)

    if not full_text.strip():
        raise ValueError("Документ порожній")

    if len(full_text.encode("utf-8")) > 8_000_000:
        raise ValueError("Для демо використайте документ до 8 MB тексту")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=450,
        chunk_overlap=60,
    )

    if transactions:
        units = [
            (
                f"Дата: {row['date']}. "
                f"Опис: {row['description']}. "
                f"Сума витрати: {row['amount']} {row['currency']}. "
                f"Категорія: {row['category']}."
            )
            for row in transactions
        ]
    else:
        units = [full_text]

    fragments = [
        fragment
        for unit in units
        for fragment in splitter.split_text(unit)
    ]

    model = get_model()

    vectors = model.embed_documents(fragments)

    chunk_records = []

    for position, (text, vector) in enumerate(zip(fragments, vectors)):
        if len(vector) != VECTOR_SIZE:
            raise ValueError("Невідповідна розмірність вкладення")

        chunk_records.append({
            "_id": f"{document_id}:{position}",
            "document_id": document_id,
            "position": position,
            "text": text,
            "embedding": vector,
            "model": MODEL_NAME,
        })

    db.documents.replace_one(
        {"_id": document_id},
        {
            "_id": document_id,
            "sha256": document_id,
            "source": source,
            "original_path": str(path),
            "full_text": full_text,
            "created_at": datetime.now(timezone.utc),
            "status": "processing",
        },
        upsert=True,
    )

    db.chunks.delete_many({"document_id": document_id})

    if chunk_records:
        db.chunks.insert_many(chunk_records)

    db.documents.update_one(
        {"_id": document_id},
        {"$set": {
            "status": "ready",
            "chunk_count": len(chunk_records),
        }},
    )

    print("ID документа:", document_id)
    print("Кількість фрагментів:", len(chunk_records))

    totals = {}

    for row in transactions:
        key = (row["currency"], row["category"])
        totals[key] = totals.get(key, Decimal("0")) + Decimal(row["amount"])

    if totals:
        print("\\nВитрати за категоріями:")

        for (currency, category), amount in sorted(totals.items()):
            print(f"{category}: {amount:.2f} {currency}")


def search_by_vector(db, vector, n=5):
    if not isinstance(vector, list) or len(vector) != VECTOR_SIZE:
        raise ValueError(f"Потрібен список із {VECTOR_SIZE} чисел")

    if any(
        type(value) not in (int, float) or not math.isfinite(value)
        for value in vector
    ):
        raise ValueError("Вектор має містити тільки скінченні числа")

    if not any(vector):
        raise ValueError("Вектор не може бути нульовим")

    if not 1 <= n <= 100:
        raise ValueError("N має бути від 1 до 100")

    candidate_limit = max(n, db.chunks.count_documents({}))

    pipeline = [
        {
            "$vectorSearch": {
                "index": VECTOR_INDEX,
                "path": "embedding",
                "queryVector": vector,
                "exact": True,
                "limit": candidate_limit,
            }
        },
        {
            "$set": {
                "score": {"$meta": "vectorSearchScore"}
            }
        },
        {
            "$lookup": {
                "from": "documents",
                "localField": "document_id",
                "foreignField": "_id",
                "as": "document",
            }
        },
        {"$match": {"document.status": "ready"}},
        {"$limit": n},
        {
            "$project": {
                "_id": 0,
                "fragment_id": "$_id",
                "document_id": 1,
                "text": 1,
                "score": 1,
            }
        },
    ]

    return list(db.chunks.aggregate(pipeline))


def main():
    parser = argparse.ArgumentParser(
        description="Аналіз та категоризація особистих витрат"
    )

    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init")

    import_command = commands.add_parser("import")
    import_command.add_argument("source")
    import_command.add_argument(
        "--ext",
        choices=[".csv", ".pdf", ".txt"],
    )

    search_command = commands.add_parser("search")

    query_group = search_command.add_mutually_exclusive_group(required=True)
    query_group.add_argument("--query")
    query_group.add_argument("--vector-file")

    search_command.add_argument("-n", type=int, default=5)

    args = parser.parse_args()

    uri = os.getenv("MONGODB_URI")

    if not uri:
        raise ValueError("Вкажіть MONGODB_URI у файлі .env")

    with MongoClient(uri, serverSelectionTimeoutMS=15000) as client:
        client.admin.command("ping")
        db = client[os.getenv("MONGODB_DB", "personal_expenses")]

        if args.command == "init":
            create_indexes(db)

        elif args.command == "import":
            ingest(db, args.source, args.ext)

        else:
            if args.query:
                vector = get_model().embed_query(args.query)
            else:
                vector = json.loads(
                    Path(args.vector_file).read_text(encoding="utf-8")
                )

            results = search_by_vector(db, vector, args.n)

            print(json.dumps(
                results,
                ensure_ascii=False,
                indent=2,
            ))


if __name__ == "__main__":
    main()
