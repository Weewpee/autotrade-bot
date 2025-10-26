# ASB Autotrade Bot

FastAPI + CCXT + Telegram. Принимает алерты с TradingView → подтверждение в Telegram → ордер на бирже (или paper).

## Установка
```bash
pip install -r requirements.txt
cp .env.example .env
# заполни ключи
uvicorn server:app --host 0.0.0.0 --port 8000
