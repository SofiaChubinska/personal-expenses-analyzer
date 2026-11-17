"use strict";

const element = (id) => document.getElementById(id);
const number = new Intl.NumberFormat("uk-UA", {minimumFractionDigits: 2, maximumFractionDigits: 2});
let source;
let filename;
let format;
let result;
let page = 0;
let version = 0;
let pending;

function amount(value) {
    return number.format(Number(value)) + " " + result.currency;
}

async function request(path, currency, question = "", signal) {
    const params = new URLSearchParams({format, exclude: Number(element("exclude").checked)});
    if (currency) params.set("currency", currency);
    if (question) params.set("q", question);
    const response = await fetch(path + "?" + params, {method: "POST", body: source, signal});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    return data;
}

function renderBars(id, rows, maxValue) {
    const container = element(id);
    container.replaceChildren();
    for (const [label, value, caption] of rows) {
        const bar = document.createElement("div");
        bar.className = "bar";
        const headings = document.createElement("div");
        const name = document.createElement("span");
        const figure = document.createElement("span");
        name.textContent = label;
        figure.textContent = caption;
        headings.append(name, figure);
        const meter = document.createElement("meter");
        meter.max = maxValue || 1;
        meter.value = value;
        meter.setAttribute("aria-label", label + " " + caption);
        bar.append(headings, meter);
        container.append(bar);
    }
}

function renderTrend() {
    const values = result[element("period").value];
    const rows = values.map(([key, value]) => [key, Number(value), amount(value)]);
    renderBars("trend", rows, Math.max(...rows.map((row) => row[1])));
}

function renderTable() {
    const table = element("transactions");
    const size = 25;
    const pages = Math.ceil(result.count / size);
    table.replaceChildren();
    for (const transaction of result.transactions.slice(page * size, (page + 1) * size)) {
        const row = table.insertRow();
        transaction.forEach((value, index) => {
            row.insertCell().textContent = index === 2 ? amount(value) : value;
        });
    }
    element("page").textContent = `${page + 1} / ${pages}`;
    element("prev").disabled = page === 0;
    element("next").disabled = page + 1 >= pages;
}

async function refresh(currency) {
    const current = ++version;
    pending?.abort();
    pending = new AbortController();
    element("report").hidden = true;
    element("status").textContent = "Обробка файлу…";
    try {
        const data = await request("/analyze", currency, "", pending.signal);
        if (current !== version) return;
        result = data;
        page = 0;
        const selector = element("currency");
        selector.replaceChildren(...result.currencies.map((value) => new Option(value, value)));
        selector.value = result.currency;
        selector.disabled = false;
        element("total").textContent = amount(result.total);
        element("count").textContent = result.count;
        element("forecast").textContent = result.forecast === null ? "Недостатньо даних" : amount(result.forecast);
        const detail = result.forecast === null ? "Потрібні три послідовні повні місяці." :
            `${result.target_month} · середнє за ${result.forecast_window.join(", ")}.`;
        const error = result.mae === null ? "" :
            ` MAE ${amount(result.mae)} за ${result.validation_count} контрольними місяцями.`;
        element("model-note").textContent = detail + error;
        renderBars("categories", result.categories.map((row) => [
            row.name, row.share, amount(row.amount) + " · " + row.share.toFixed(1) + "%",
        ]), 100);
        renderTrend();
        const tips = result.recommendations.map((tip) => {
            const item = document.createElement("li");
            item.textContent = tip;
            return item;
        });
        element("recommendations").replaceChildren(...tips);
        if (!tips.length) element("recommendations").textContent = "Для цих даних рекомендацій немає.";
        element("answer").textContent = "";
        element("mode").textContent = "";
        element("table-caption").textContent = `Операції (${result.count})`;
        renderTable();
        element("status").textContent = filename;
        element("report").hidden = false;
    } catch (error) {
        if (current === version) {
            element("currency").disabled = true;
            element("status").textContent = error.message;
        }
    }
}

element("file").addEventListener("change", (event) => {
    const file = event.target.files[0];
    if (!file) return;
    source = file;
    filename = file.name;
    format = file.name.toLowerCase().endsWith(".xlsx") ? "xlsx" : "csv";
    refresh();
});

function loadExample(button, url, label, exclude) {
    element(button).addEventListener("click", async () => {
        const current = ++version;
        pending?.abort();
        element("report").hidden = true;
        element("status").textContent = "Завантаження набору…";
        try {
            const response = await fetch(url);
            if (!response.ok) throw new Error("Не вдалося завантажити набір.");
            const file = await response.blob();
            if (current !== version) return;
            source = file;
            filename = label;
            format = "csv";
            element("file").value = "";
            element("exclude").checked = exclude;
            refresh();
        } catch (error) {
            if (current === version) element("status").textContent = error.message;
        }
    });
}
loadExample("sample", "/example", "Вихідні операції за 1–8 жовтня 2026 року", true);
loadExample("demo", "/demo", "Демонстраційна історія · 8 вихідних і 30 синтетичних операцій", false);
element("currency").addEventListener("change", (event) => refresh(event.target.value));
element("exclude").addEventListener("change", () => {
    if (source) refresh(element("currency").value);
});
element("period").addEventListener("change", renderTrend);
element("prev").addEventListener("click", () => { page--; renderTable(); });
element("next").addEventListener("click", () => { page++; renderTable(); });
element("ask").addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = element("question").value.trim();
    if (!question) return;
    const current = version;
    const button = event.target.querySelector("button");
    button.disabled = true;
    element("mode").textContent = "";
    element("answer").textContent = "Обробка запитання…";
    try {
        const reply = await request("/ask", result.currency, question);
        if (current === version) {
            element("answer").textContent = reply.answer;
            element("mode").textContent = reply.mode;
        }
    } catch (error) {
        if (current === version) element("answer").textContent = error.message;
    } finally {
        button.disabled = false;
    }
});
