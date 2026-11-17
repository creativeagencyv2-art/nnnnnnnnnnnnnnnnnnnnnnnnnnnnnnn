# Creative Agency Telegram Bot (FINAL)

Customer bot + UPI top-up with admin approval + Telegram admin panel + live Silvex supplier integration.

## Fresh setup on Windows
1. Install Python 3.12 (terminal: winget install Python.Python.3.12), restart VS Code.
2. Open this folder in VS Code -> Terminal -> New Terminal.
3. py -3.12 -m venv venv
4. venv\Scripts\activate
5. pip install -r requirements.txt
6. copy .env.example .env   then fill .env (BOT_TOKEN, ADMIN_IDS, UPI_ID, SUPPORT_USERNAME, SILVEX_API_KEY)
7. python main.py

## First-time supplier setup (in Telegram, as admin)
1. /api_check      -> confirms the key works and shows what the supplier returns
2. /rate 90        -> your USD to INR rate (supplier prices are in USD)
3. /markup 30      -> profit % added to NEW plans
4. /admin -> Sync Products -> then review prices in Products & Plans
5. Test with ONE small real order before announcing the bot.

## Tests (use a local fake supplier, never the real one)
python -m tests.test_wallet_orders
python -m tests.test_payments
python -m tests.test_silvex
