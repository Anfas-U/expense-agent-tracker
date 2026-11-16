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
- **Pocket Money Dashboard**: Branded local dashboard with monthly insights, category filters, merchant search, chat, and CSV export.

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

### 7. Run the Local Web Dashboard
Install the updated requirements and set `WEB_USERNAME` plus a unique
`WEB_PASSWORD` of at least 16 characters in `.env`, then start the web app:
```powershell
.\.venv\Scripts\pip.exe install -r requirements.txt
.\.venv\Scripts\python.exe -m src.interfaces.web
```
Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser. The
**Pocket Money** dashboard shows monthly totals, category spending, searchable
and filterable recent expenses, and an expense assistant. Use **Export CSV** to
download all saved expenses. Without an LLM API key in `.env`, chat uses the
project's mock fallback. The web interface requires HTTP Basic Authentication:
set `WEB_USERNAME` and a unique `WEB_PASSWORD` of at least 16 characters in
`.env`; your browser will ask you to sign in. For public hosting, use a host
that provides HTTPS. This is still a single-user app and does not separate data
between multiple users.

On Windows, you can instead double-click `Open Pocket Money Dashboard.bat`.
It starts the local server if needed and opens the dashboard in your browser.
Keep the server's console window open while using the dashboard; closing it
stops the local server.

### 8. Host the Dashboard Publicly on Render

The dashboard can be hosted at a public Render URL while remaining protected
by its single-user username and password. Anyone who has the URL can reach the
login prompt, but they cannot view expenses or use the dashboard without the
credentials. This app is not a multi-user service; do not remove authentication
or share the login for public access.

1. Push this project to a **private GitHub repository**. Do not commit `.env`,
   `.venv`, or the `data` directory; they are excluded by `.gitignore`.
2. In Render, choose **New → Blueprint** and connect the repository. Render
   reads the included `render.yaml` to configure the Python web service, login,
   and persistent disk. This setup uses a paid service plan because SQLite
   needs persistent storage.
3. Review the generated `WEB_PASSWORD` in the Render service's **Environment**
   settings and save it somewhere secure. The username is `owner`.
4. Deploy the service. When deployment finishes, open its `onrender.com` URL
   and sign in. Render provides HTTPS for the hosted URL.
5. To use live AI chat, add `LLM_API_KEY` in the Render service's environment
   settings (and `LLM_PROVIDER` if you use a provider other than Gemini), then
   redeploy. Without an API key, chat uses the mock fallback.

The hosted app stores its SQLite database, CSV data, and receipts on the
attached disk mounted at `/var/data`. Your existing local `data` directory is
not uploaded, so the hosted database starts empty. Back up your local files
before deployment. The Render disk preserves data through restarts and
deployments, but keep separate backups for important expense records.
