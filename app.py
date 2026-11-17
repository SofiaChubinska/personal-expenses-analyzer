"""Run the dashboard on the loopback interface."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zipfile import BadZipFile

from assistant import answer
from expenses import analyze, read_transactions

ROOT = Path(__file__).resolve().parent
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/dashboard.js": ("dashboard.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/example": ("data/example.csv", "text/csv; charset=utf-8"),
    "/demo": ("data/demo_history.csv", "text/csv; charset=utf-8"),
}
MAX_FILE = 4 * 1024 * 1024


class Dashboard(BaseHTTPRequestHandler):
    def log_request(self, code="-", size="-"):
        # The URL may contain a personal finance question.
        pass

    def valid_request(self):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host")
        return host in hosts and self.headers.get("Origin") in (None, f"http://{host}")

    def respond(self, status, data, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, status, payload):
        self.respond(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        entry = STATIC.get(urlsplit(self.path).path)
        if not self.valid_request() or entry is None:
            self.send_error(404)
            return
        filename, content_type = entry
        self.respond(200, (ROOT / filename).read_bytes(), content_type)

    def do_POST(self):
        request = urlsplit(self.path)
        if not self.valid_request() or request.path not in {"/analyze", "/ask"}:
            self.send_error(404)
            return
        options = parse_qs(request.query)
        question = options.get("q", [""])[0].strip()
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_FILE:
                raise ValueError("Завантажте непорожній файл розміром до 4 МіБ.")
            if request.path == "/ask" and not 0 < len(question) <= 400:
                raise ValueError("Поставте запитання довжиною від 1 до 400 символів.")
            transactions = read_transactions(self.rfile.read(size), options.get("format") == ["xlsx"])
            currencies = sorted({row[3] for row in transactions})
            currency = options.get("currency", currencies)[0]
            summary = analyze(transactions, currency, options.get("exclude") == ["1"])
            if request.path == "/ask":
                records = summary.pop("transactions")
                self.send_json(200, answer(question, summary, records[:20]))
            else:
                self.send_json(200, {"currencies": currencies, **summary})
        except ImportError:
            self.send_json(400, {"error": "Для Excel встановіть залежності з requirements.txt."})
        except (BadZipFile, UnicodeError, KeyError, OSError):
            self.send_json(400, {"error": "Перевірте формат файлу. Потрібен CSV UTF-8 або Excel XLSX."})
        except ValueError as error:
            self.send_json(400, {"error": str(error)})


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", args.port), Dashboard) as server:
        print(f"Відкрийте http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
