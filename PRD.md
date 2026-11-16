# Product Requirements Document: Expense Tracker Agent

| | |
|---|---|
| **Version** | 0.1 (Draft) |
| **Date** | 11 Nov 2026 |
| **Status** | Draft for review |
| **Owner** | TBD |

---

## 1. Overview

### 1.1 Summary
The Expense Tracker Agent is an AI agent that lets a user record spending in the easiest way possible: by sending a short text message ("spent 450 on groceries") or a photo of a receipt. The agent extracts the details, categorizes the expense, and logs it to a spreadsheet. The user can later ask questions such as "How much did I spend on food this month?" in plain language.

### 1.2 Problem Statement
Most people do not track expenses because it is tedious. Opening an app, picking a category, typing an amount, and choosing a date takes too many steps, so people stop after a few days. Paper receipts get lost, and cash and UPI spending is especially hard to account for.

### 1.3 Proposed Solution
A conversational agent that accepts messy, natural input (text, voice transcript, receipt image), turns it into structured data, and writes it to a spreadsheet the user owns. The user spends a few seconds per expense and gets an organized record plus on-demand summaries.

### 1.4 Goals
- Reduce the effort to log one expense to under 10 seconds.
- Categorize expenses correctly without manual correction in at least 90% of cases.
- Keep the user's data in a spreadsheet they can open, edit, and export at any time.
- Answer basic spending questions accurately.

### 1.5 Non-Goals (v1)
- Connecting to bank accounts or reading bank SMS automatically.
- Moving money, making payments, or any financial transaction.
- Investment tracking, tax filing, or accounting for businesses.
- Multi-user shared budgets (considered for later versions).

---

## 2. Target Users

### 2.1 Primary Persona: The Everyday Spender
A student or working professional who spends mostly through UPI, cards, and cash, wants to know where the money goes, and will not tolerate a heavy app.

### 2.2 Secondary Persona: The Freelancer / Small Earner
Needs to separate personal and work expenses and keep receipts for later reference.

### 2.3 Key User Needs
- Fast, low-friction logging.
- Trust that amounts and categories are right.
- Ownership of data and easy export.
- Simple insights without building pivot tables.

---

## 3. User Stories

| ID | As a... | I want to... | So that... | Priority |
|---|---|---|---|---|
| US-1 | User | send "spent 450 on groceries" | it is logged without opening a spreadsheet | P0 |
| US-2 | User | send a photo of a receipt | amount, merchant, and date are extracted for me | P0 |
| US-3 | User | have expenses auto-categorized | I do not pick a category each time | P0 |
| US-4 | User | see all expenses in a spreadsheet | I can review, sort, and edit them myself | P0 |
| US-5 | User | correct a wrong entry in chat ("change that to 540") | mistakes are easy to fix | P0 |
| US-6 | User | ask "how much did I spend on food in October?" | I get answers without manual calculation | P1 |
| US-7 | User | set a monthly budget per category | I get warned when I am close to the limit | P1 |
| US-8 | User | log several expenses in one message | batch entry is quick | P1 |
| US-9 | User | log expenses by voice | I can do it hands-free | P2 |
| US-10 | User | receive a weekly summary | I stay aware of my habits | P2 |
| US-11 | User | record recurring expenses (rent, subscriptions) | I do not re-enter them every month | P2 |

---

## 4. Functional Requirements

### 4.1 Input Handling
- **FR-1:** Accept free-form text messages describing one or more expenses.
- **FR-2:** Accept receipt images (JPG, PNG) and extract merchant, date, total amount, and line items where legible.
- **FR-3:** Support amounts in INR by default, with the currency symbol, "Rs", or "rupees" recognized. Allow a configurable default currency.
- **FR-4:** Interpret relative dates ("yesterday", "last Friday") using the user's timezone (default: Asia/Kolkata). If no date is given, use today.
- **FR-5:** Support English at launch. Malayalam and Hindi input are targeted for a later release.

### 4.2 Extraction and Categorization
- **FR-6:** Extract these fields for every expense: `date`, `amount`, `currency`, `merchant/description`, `category`, `payment method` (optional), `notes` (optional).
- **FR-7:** Assign a category from a default list: Groceries, Dining, Transport, Fuel, Utilities, Rent, Shopping, Health, Entertainment, Education, Travel, Subscriptions, Other.
- **FR-8:** Allow users to add custom categories and define rules ("Swiggy is always Dining").
- **FR-9:** Learn from corrections: when a user changes a category for a merchant, apply that choice to future entries from the same merchant.
- **FR-10:** If confidence in the amount or category is low, ask a short clarifying question instead of guessing.

### 4.3 Confirmation and Editing
- **FR-11:** After logging, reply with a one-line confirmation, for example: "Logged ₹450, Groceries, 11 Nov."
- **FR-12:** Allow edit and delete by chat command, referring to the most recent entry or to a specific entry.
- **FR-13:** For receipt images, show the extracted values and allow the user to correct them before or after saving.
- **FR-14:** Detect probable duplicates (same amount, merchant, and date within a short window) and ask before saving.

### 4.4 Spreadsheet Logging
- **FR-15:** Write each expense as a new row in a spreadsheet the user owns (Google Sheets in v1, with CSV/XLSX export).
- **FR-16:** Use a fixed schema (see Section 6) with one sheet for raw entries and a second for monthly summaries.
- **FR-17:** Preserve the user's manual edits to the sheet; the agent must not overwrite rows it did not create in the current action.
- **FR-18:** Store a link or reference to the receipt image for entries created from receipts.

### 4.5 Queries and Insights
- **FR-19:** Answer natural-language questions about spending by category, time period, and merchant.
- **FR-20:** Compute answers from the spreadsheet data using actual calculation, not estimation by the model.
- **FR-21:** Generate a monthly summary with totals per category and comparison to the previous month.
- **FR-22:** Support budgets per category with alerts at 80% and 100% of the limit.

### 4.6 Account and Settings
- **FR-23:** Let the user set default currency, timezone, categories, and budgets.
- **FR-24:** Let the user export all data and delete all data on request.

---

## 5. Non-Functional Requirements

| Area | Requirement |
|---|---|
| **Latency** | Text entry logged and confirmed within 5 seconds (p95). Receipt entry within 15 seconds (p95). |
| **Accuracy** | Amount extraction correct in at least 98% of text entries and 92% of clear receipts. Category correct in at least 90% of entries. |
| **Reliability** | No silent failures. If logging fails, the user is told and the entry is retried or preserved. |
| **Privacy** | Financial data and receipt images are used only to serve the user. No sale or sharing of data. Clear consent for image processing. |
| **Security** | Encrypted storage and transport. Least-privilege access to the user's spreadsheet (only the designated file). |
| **Cost** | Average model and infrastructure cost per logged expense stays within a defined budget (target to be set after prototype). |
| **Data ownership** | All data lives in the user's spreadsheet; the agent holds only the minimum needed to operate. |

---

## 6. Data Model

### 6.1 Expenses Sheet (one row per expense)

| Column | Type | Description |
|---|---|---|
| `id` | string | Unique entry ID |
| `date` | date | Date of the expense |
| `amount` | number | Amount spent |
| `currency` | string | ISO code, default INR |
| `merchant` | string | Merchant or short description |
| `category` | string | One of the configured categories |
| `payment_method` | string | Cash, UPI, Card, Other (optional) |
| `notes` | string | Free text (optional) |
| `source` | string | `text`, `receipt`, `voice`, `recurring` |
| `receipt_url` | string | Link to stored receipt image (optional) |
| `confidence` | number | Model confidence, 0 to 1 |
| `created_at` | datetime | When the agent logged it |

### 6.2 Summary Sheet
Monthly totals per category, budget, amount remaining, and percent used. Built with spreadsheet formulas so it stays correct when the user edits entries.

---

## 7. Agent Design

### 7.1 Core Loop
**Constraint:** the loop is implemented by hand in plain Python. Agent frameworks (LangChain, LlamaIndex, CrewAI, etc.) are not used, so that every step of the model-tool cycle is visible and testable.

1. Receive a message or image from the user.
2. Decide the intent: log expense, edit, delete, query, or settings change.
3. Call the appropriate tool.
4. Check the result and confirm to the user in a short reply.

### 7.2 Tools

| Tool | Purpose |
|---|---|
| `parse_expense` | Turn text into structured fields |
| `extract_receipt` | Read an image and return structured fields |
| `categorize` | Assign a category using rules, history, then the model |
| `append_row` | Add an expense to the spreadsheet |
| `update_row` / `delete_row` | Edit or remove an entry |
| `query_expenses` | Filter and aggregate rows with code, not the model |
| `get_settings` / `set_settings` | Read and change preferences and budgets |

### 7.3 Guardrails
- The agent only reads and writes the designated spreadsheet.
- It never performs payments or transfers.
- Deleting multiple rows or all data requires explicit confirmation.
- Numbers in replies always come from tool results, never from model memory.
- When unsure, it asks instead of guessing.

### 7.4 Memory
- Merchant-to-category mappings per user.
- User preferences (currency, categories, budgets).
- Recent entries (for "change that" and "delete the last one" commands).

---

## 8. User Experience

### 8.1 Interface (v1)
A chat interface. The recommended first channel is Telegram or WhatsApp, since both support text and images and match how people already message. A simple web chat is an acceptable alternative for the prototype.

### 8.2 Example Conversations

**Text entry**
> **User:** spent 450 on groceries
> **Agent:** Logged ₹450, Groceries, today.

**Multiple entries**
> **User:** auto 80, coffee 120 and lunch 250 yesterday
> **Agent:** Logged 3 expenses: ₹80 Transport, ₹120 Dining, ₹250 Dining (yesterday). Total ₹450.

**Receipt**
> **User:** *(sends photo)*
> **Agent:** Found: Big Bazaar, ₹1,284, 10 Nov. Category: Groceries. Save it?

**Correction**
> **User:** change that to 1,248
> **Agent:** Updated to ₹1,248.

**Query**
> **User:** how much on food this month?
> **Agent:** ₹6,420 across Groceries (₹3,900) and Dining (₹2,520), up 8% from last month.

**Clarification**
> **User:** paid 2000 to Ravi
> **Agent:** What was this for? (Rent, Shopping, Other, or tell me.)

### 8.3 Onboarding
1. User starts the chat and connects their Google account.
2. Agent creates the spreadsheet with the correct schema (or links an existing one).
3. User sets currency and optionally budgets.
4. A guided first entry demonstrates the flow.

---

## 9. Success Metrics

| Metric | Target |
|---|---|
| Time to log an expense | Under 10 seconds |
| Entries needing correction | Under 10% |
| Weekly active users still logging after 4 weeks | At least 40% of those who onboarded |
| Average entries per active user per week | At least 10 |
| Query answer accuracy (on a test set) | At least 95% |
| User-reported trust in numbers (survey) | At least 4 out of 5 |

---

## 10. Evaluation Plan

- Build a test set of at least 100 text expense messages (including slang, mixed formats, and typos) with known correct outputs.
- Build a test set of at least 50 receipt images (printed, crumpled, handwritten, low light, UPI screenshots).
- Build a set of 30 query questions with answers verified against a known spreadsheet.
- Measure field-level accuracy (amount, date, category) and track results on every change to the prompt or model.
- Run a small beta with 10 to 20 real users for 4 weeks and collect feedback.

---

## 11. Release Plan

| Phase | Scope | Timeline |
|---|---|---|
| **Phase 0: Prototype** | Text logging to Google Sheets, single user, basic categories | Week 1 |
| **Phase 1: MVP** | Receipt scanning, edit/delete, duplicate detection, summary sheet | Weeks 2 to 3 |
| **Phase 2: Insights** | Natural-language queries, budgets and alerts, weekly summary | Weeks 4 to 5 |
| **Phase 3: Beta** | Onboarding flow, privacy controls, 10 to 20 users, evaluation | Weeks 6 to 8 |
| **Phase 4: Expansion** | Voice input, Malayalam/Hindi, recurring expenses, UPI screenshot parsing | Later |

---

## 12. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Wrong amount extracted from a blurry receipt | Loss of trust | Show extracted values for confirmation; ask when confidence is low |
| Incorrect category | Poor insights | Merchant rules, learning from corrections, easy edit command |
| Model does arithmetic wrongly in summaries | Incorrect totals | Compute with code or spreadsheet formulas only |
| Sensitive financial data exposure | Privacy harm | Encryption, minimal data retention, least-privilege access, clear consent |
| Agent edits or deletes the wrong row | Data loss | Confirm destructive actions; keep an audit log; keep row IDs |
| API or spreadsheet outages | Lost entries | Queue and retry; tell the user if an entry is pending |
| Cost grows with image usage | Budget overrun | Compress images, cache results, monitor cost per entry |
| Users send ambiguous messages | Bad data | Ask short clarifying questions |

---

## 13. Dependencies and Assumptions

**Dependencies**
- A language model with vision and tool use (for example, the Claude API).
- Google Sheets API and Google OAuth.
- A messaging platform API (Telegram Bot API or WhatsApp Business API).
- Storage for receipt images.

**Assumptions**
- Users are comfortable granting access to one spreadsheet.
- Most users log in English or a mix of English and a local language.
- Receipts are mostly printed or digital (UPI screenshots, e-bills).

---

## 14. Open Questions

1. Which messaging channel should be first: Telegram, WhatsApp, or web chat?
2. Should receipt images be stored long term or deleted after extraction?
3. Is one default category list enough, or should it vary by user type?
4. Should the agent support multiple currencies in v1 or only INR?
5. What is the acceptable cost per entry for a free tier?
6. Will there be a hosted service for others, or is this a personal tool first?

---

## 15. Appendix: Suggested Tech Stack

| Layer | Option |
|---|---|
| Model | Claude API with tool use and vision |
| Agent framework | None. A hand-written agent loop in plain Python, using only the model provider's SDK for API calls. No LangChain, LlamaIndex, or similar agent libraries. |
| Backend | Python (FastAPI) or Node.js |
| Spreadsheet | Google Sheets API (export to CSV/XLSX) |
| Messaging | Telegram Bot API (easiest to start), WhatsApp later |
| Storage | Cloud object storage for receipts; a small database for user settings |
| Hosting | Any serverless or small container host |
