# Pocket Money

**Pocket Money** is a personal expense tracker designed to make recording expenses effortless: send a short text message (*"spent 450 on groceries"*) or a photo of a receipt, and the agent extracts details, categorizes the spending, checks for duplicates, and logs it to a Google Spreadsheet or local database.

Built according to [PRD.md](file:///c:/Users/anfas/Desktop/expense%20agent%20tracker/PRD.md) and fully tracked in [task.md](file:///c:/Users/anfas/Desktop/expense%20agent%20tracker/task.md).

---

## Key Features

- **Hand-Crafted Pure Python Agent Loop**: Built from scratch without agent frameworks (no LangChain, no LlamaIndex) for transparency, determinism, and testability.
- **Natural Language Text Parsing**: Handles single items, multi-item batches (*"auto 80, coffee 120 and lunch 250 yesterday"*), relative dates (*"yesterday"*, *"last Friday"* in `Asia/Kolkata` timezone), and currency symbols (₹, Rs, INR).
- **Tri-Level Categorization Hierarchy**:
  1. Learned User Rules (automatically memorized from corrections)
  2. Exact Merchant Rules (*"Swiggy is always Dining"*)
  3. Taxonomy Keyword Matching (with word-boundary regex)
  4. Clarification trigger for ambiguous cases (*"paid 2000 to Ravi"*)
- **Multimodal Receipt Extraction**: Auto-rotates EXIF orientation, compresses large images to minimize token cost and latency, and prompts with confirmation before saving.
- **Duplicate Detection**: 48-hour sliding window check on identical amounts and merchants to avoid accidental double-counting.
- **Deterministic Insights Engine**: Computes sums, counts, and category breakdowns via deterministic code—never hallucinating math numbers.
- **UPI Screenshot OCR**: Specialized parsing for Google Pay, PhonePe, and Paytm receipts with transaction ID extraction.
- **Telegram Voice Notes**: Native voice note ingestion (`.ogg`) and speech-to-text transcription.
- **Multi-Currency Normalizer**: Detects foreign currencies ($/USD, €/EUR, £/GBP, AED), converts to base currency (INR), and preserves original notes.
- **Recurring Expenses Scheduler**: Monthly automated logging for rent, subscriptions, and recurring bills.
- **Dual Storage**: Seamless zero-configuration local CSV/SQLite storage out-of-the-box, with instant Google Sheets synchronization when credentials are provided.
- **Multiple Channels**: Interactive developer CLI and full Telegram Bot integration.
- **Pocket Money Dashboard**: Branded dashboard with monthly insights, all-time category totals, monthly budget progress and alerts, recurring-bill reminders, expense editing, private account backups, category filters, merchant search, chat, and CSV export.

---

## Quick Start

### 1. Activate Virtual Environment
```powershell
.\.venv\Scripts\Activate.ps1
```

### 2. Configure Environment (Optional)
Copy `.env.example` to `.env` and fill in your keys:
```powershell
Copy-Item .env.example .env
```
Key settings:
- `LLM_PROVIDER`: `gemini` or `anthropic`
- `LLM_API_KEY`: Your Gemini or Anthropic API key
- `TELEGRAM_BOT_TOKEN`: From Telegram's `@BotFather`
- `GOOGLE_CREDENTIALS_PATH`: Path to `service_account.json`

*(Note: The system works out-of-the-box with a local fallback even without API keys!)*

### 3. Launch Interactive Developer CLI
```powershell
python main.py --mode cli
```
Try typing:
- `spent 450 on groceries`
- `auto 80, coffee 120 and lunch 250 yesterday`
- `how much on food this month?`
- `change that to 540`
- `/summary`
- `/receipt path/to/receipt.jpg`

### 4. Run Telegram Bot Daemon
```powershell
python main.py --mode telegram
```

### 5. Run Automated Tests
```powershell
.\.venv\Scripts\pytest.exe -v
```

### 6. Run 100-Sample Benchmark Evaluation
```powershell
python tests/evaluation/run_eval.py
```
Outputs field-level accuracy and latency benchmarks against the PRD targets.

### 7. Run the Multi-User Web App
The public web app requires a PostgreSQL database. For local testing, create a
database with a managed provider such as Neon or Supabase, or use a local
PostgreSQL server. Keep the connection URL private:

```powershell
Copy-Item .env.example .env
# Set DATABASE_URL and COOKIE_SECURE=false in your private .env for local HTTP.
```

Install dependencies and run the web server:
```powershell
.\.venv\Scripts\pip.exe install -r requirements.txt
.\.venv\Scripts\python.exe -m src.interfaces.web
```
Open [http://127.0.0.1:8000](http://127.0.0.1:8000), create an account, and
sign in. Each account has independent expenses, budgets, settings, recurring
expenses, receipts, and sessions. Passwords are stored as scrypt hashes and
sessions are random, revocable PostgreSQL records. Browser sessions last 30
days. Records are not automatically expired or removed; they remain until the
account owner deletes them or deletes their account. `DELETE /api/account`
requires the account password and permanently removes that account's data.
The dashboard lets each user set monthly budgets, see alerts at 80% and over
budget, manage recurring bills with due-date reminders, and edit recent
expenses. Recurring reminders appear when the user opens the dashboard; they
do not send email or text messages.

Users can download a private JSON backup from the dashboard. It includes their
expenses, budgets, merchant rules, recurring bills, settings, and receipt
images. Receipt images are limited to 25 MiB per download. This is a manual
per-account export, not an automatic database backup; configure automatic
backup and restore retention with the hosted PostgreSQL provider as well.

The app's hosted tables are created automatically. For a public deployment you
need:

- A managed PostgreSQL database (for example Neon or Supabase) and its private
  `DATABASE_URL` connection string.
- An application host that runs Python and supports HTTPS (for example Render,
  Railway, or Fly.io). Set its start command to
  `python -m uvicorn src.interfaces.web:app --host 0.0.0.0 --port $PORT`.
- `COOKIE_SECURE=true` in production. Set `DATABASE_URL` as a secret in the
  host's environment; do not put credentials in source control or build logs.
- Optionally, `LLM_PROVIDER`, `LLM_API_KEY`, and `LLM_MODEL` for live assistant
  responses. Without an API key the app uses the project's local mock agent.

For the included Render blueprint, create a Neon PostgreSQL project first and
copy its pooled connection URL from the Neon dashboard. In Render, create a
new Blueprint from this GitHub repository; Render reads `render.yaml` and asks
for `DATABASE_URL`. Paste the connection URL into Render's secret field (not
into this repository). Deploy, wait for the service health check, then open its
`onrender.com` URL and create the first account.

`DATABASE_URL` and `COOKIE_SECURE` are the web app's required deployment
settings. `DEFAULT_CURRENCY` and `DEFAULT_TIMEZONE` provide per-account initial
defaults; users can override them through `/api/settings`. Receipt images are
stored in the PostgreSQL database and served only through authenticated,
owner-scoped routes. The host/database plan must retain backups and data for
the period you promise users; this application does not implement a 90-day
cleanup job.

To move an existing single-user SQLite database, use the optional one-time
migration command after creating the destination account:

```powershell
python -m src.storage.migrate_sqlite --username your-username --sqlite-path data/expense_tracker.db
```

It copies that SQLite file's expenses, budgets, learned merchant rules, and
recurring expenses into the named PostgreSQL account. Receipt images at
accessible local paths are copied when they are valid images no larger than
5 MB; other receipt references are left as-is. The command skips records
already present and leaves the source file untouched. Verify the destination
before removing any local copies or backups. Configure the application host's
rate limits for `/signup` and `/login` before opening registration to the
public.

On Windows, you can instead double-click `Open Pocket Money Dashboard.bat`.
It starts the local server if needed and opens the dashboard in your browser.
Keep the server's console window open while using the dashboard; closing it
stops the local server.
