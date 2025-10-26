import os, json, time, sqlite3, requests
from fastapi import FastAPI, Request, Header, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
import ccxt

load_dotenv()

EXCHANGE = os.getenv("EXCHANGE", "binance")
API_KEY  = os.getenv("API_KEY", "")
API_SEC  = os.getenv("API_SECRET", "")
PAPER    = os.getenv("PAPER", "true").lower() == "true"

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT  = int(os.getenv("TELEGRAM_CHAT_ID", "0"))

TV_SECRET = os.getenv("TV_WEBHOOK_SECRET", "")  # если хочешь проверку заголовка
DEFAULT_QTY = float(os.getenv("DEFAULT_QTY", "0.001"))

# ccxt биржа
exchange = getattr(ccxt, EXCHANGE)({
    "apiKey": API_KEY,
    "secret": API_SEC,
    "enableRateLimit": True
})

# БД-журнал
DB = "trades.db"
conn = sqlite3.connect(DB, check_same_thread=False)
conn.execute("""CREATE TABLE IF NOT EXISTS journal(
    id TEXT PRIMARY KEY,
    ts INTEGER,
    signal TEXT,
    symbol TEXT,
    price REAL,
    sl REAL,
    tp TEXT,
    qty REAL,
    status TEXT,
    details TEXT
)""")
conn.execute("""CREATE TABLE IF NOT EXISTS pending(
    id TEXT PRIMARY KEY,
    payload TEXT,
    ts INTEGER
)""")
conn.commit()

app = FastAPI(title="ASB Autotrade")

class Signal(BaseModel):
    signal: str
    symbol: str
    time: str
    price: float
    sl: float | None = None
    tp: list[float] | None = None

def tg_send(text, reply_markup=None):
    if not TG_TOKEN or not TG_CHAT: 
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = {"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"}
    if reply_markup: data["reply_markup"] = json.dumps(reply_markup)
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print("TG send error:", e)

def tg_answer(cb_id, text):
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/answerCallbackQuery",
                      data={"callback_query_id": cb_id, "text": text}, timeout=10)
    except Exception as e:
        print("TG answer error:", e)

def place_order(symbol: str, side: str, amount: float):
    if PAPER:
        return {"status": "paper", "symbol": symbol, "side": side, "amount": amount}
    return exchange.create_market_order(symbol, side, amount)

@app.post("/tv")
async def tv(request: Request, x_tv_secret: str | None = Header(default=None)):
    if TV_SECRET and x_tv_secret != TV_SECRET:
        raise HTTPException(401, "Unauthorized")
    payload = await request.json()
    s = Signal(**payload)
    sig_id = f"{int(time.time())}-{abs(hash(json.dumps(payload)))%10**9}"

    conn.execute("INSERT OR REPLACE INTO pending(id,payload,ts) VALUES(?,?,?)",
                 (sig_id, json.dumps(payload), int(time.time())))
    conn.commit()

    text = (f"⚡️ <b>Сигнал</b>: {s.signal.upper()}\n"
            f"🪙 <b>{s.symbol}</b> @ <b>{s.price:.4f}</b>\n"
            f"🛡 SL: {s.sl}\n🎯 TP: {', '.join(map(lambda x: f'{x:.4f}', s.tp or []))}\n"
            f"ID: <code>{sig_id}</code>")
    kb = {"inline_keyboard":[
        [{"text":"✅ BUY", "callback_data":f"approve:{sig_id}:buy"},
         {"text":"❌ Пропустить", "callback_data":f"reject:{sig_id}"}]
    ]}
    tg_send(text, kb)
    return {"ok": True, "id": sig_id}

@app.post("/tg")  # укажешь этот URL как webhook у своего ТГ-бота
async def tg_update(update: dict):
    if "callback_query" not in update: 
        return {"ok": True}
    cb = update["callback_query"]
    data = cb["data"]
    cb_id = cb["id"]
    if data.startswith("approve:"):
        _, sig_id, side = data.split(":")
        row = conn.execute("SELECT payload FROM pending WHERE id=?", (sig_id,)).fetchone()
        if not row:
            tg_answer(cb_id, "Не найдено/уже выполнено.")
            return {"ok": False}
        s = Signal(**json.loads(row[0]))
        res = place_order(s.symbol, side, DEFAULT_QTY)
        conn.execute("INSERT OR REPLACE INTO journal VALUES(?,?,?,?,?,?,?,?,?,?)",
            (sig_id, int(time.time()), s.signal, s.symbol, s.price, s.sl or 0.0,
             json.dumps(s.tp or []), DEFAULT_QTY, "executed", json.dumps(res)))
        conn.execute("DELETE FROM pending WHERE id=?", (sig_id,))
        conn.commit()
        tg_answer(cb_id, "Исполнено.")
        tg_send(f"✅ {side.upper()} {s.symbol} qty={DEFAULT_QTY}\nID: <code>{sig_id}</code>")
        return {"ok": True}

    if data.startswith("reject:"):
        _, sig_id = data.split(":")
        row = conn.execute("SELECT payload FROM pending WHERE id=?", (sig_id,)).fetchone()
        if row:
            s = Signal(**json.loads(row[0]))
            conn.execute("INSERT OR REPLACE INTO journal VALUES(?,?,?,?,?,?,?,?,?,?)",
                (sig_id, int(time.time()), s.signal, s.symbol, s.price, s.sl or 0.0,
                 json.dumps(s.tp or []), 0.0, "rejected", "{}"))
            conn.execute("DELETE FROM pending WHERE id=?", (sig_id,))
            conn.commit()
        tg_answer(cb_id, "Отменено.")
        tg_send(f"🚫 Отклонено\nID: <code>{sig_id}</code>")
        return {"ok": True}

    return {"ok": True}
