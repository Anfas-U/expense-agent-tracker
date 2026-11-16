"""Telegram Bot interface for Expense Tracker Agent."""

import logging
from pathlib import Path
from typing import Optional
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config.settings import settings
from src.agent.core import ExpenseAgent
from src.tools.query_engine import query_expenses
from src.tools.receipt_scanner import extract_receipt
from src.tools.voice_transcriber import transcribe_audio_file

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


class ExpenseTelegramBot:
    """Telegram Bot wrapper for the Expense Tracker Agent."""

    def __init__(self, token: Optional[str] = None):
        self.token = token or settings.telegram_bot_token
        self.agent = ExpenseAgent()

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler for /start command (PRD Section 8.3 Onboarding)."""
        welcome_text = (
            "👋 Welcome to your Expense Tracker Agent!\n\n"
            "Logging expenses is as simple as sending a quick text or photo:\n"
            "• 'spent 450 on groceries'\n"
            "• 'auto 80, coffee 120 and lunch 250 yesterday'\n"
            "• Send a photo of any receipt or UPI screenshot\n"
            "• 'how much on food this month?'\n"
            "• 'change that to 540'\n\n"
            "Type /summary anytime to see your monthly budget and totals."
        )
        await update.message.reply_text(welcome_text)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler for /help command."""
        help_text = (
            "ℹ️ How to use Expense Tracker:\n\n"
            "1. Text Logging: Just type what you spent:\n"
            "   'petrol 500 via UPI'\n"
            "   'swiggy 340'\n"
            "2. Receipts: Take a photo and send it here.\n"
            "3. Queries: Ask about your habits:\n"
            "   'how much did I spend this week?'\n"
            "4. Edit/Delete:\n"
            "   'change that to 400'\n"
            "   'delete the last one'\n\n"
            "Commands:\n"
            "/summary - Monthly overview\n"
            "/help - Show this guide"
        )
        await update.message.reply_text(help_text)

    async def summary_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler for /summary command."""
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        q_res = query_expenses(time_period="this_month")
        
        lines = [
            f"📊 <b>Monthly Summary</b>",
            f"Total: <b>₹{q_res.get('total_amount'):,.2f}</b> ({q_res.get('entry_count')} transactions)",
            "",
        ]
        breakdown = q_res.get("category_breakdown", {})
        if breakdown:
            lines.append("<b>Breakdown:</b>")
            for cat, amt in breakdown.items():
                lines.append(f"• {cat}: ₹{amt:,.2f}")
            lines.append("")

        for alert in q_res.get("budget_alerts", []):
            lines.append(f"⚠️ {alert}")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Process incoming text message through the pure Python agent loop."""
        user_text = update.message.text
        if not user_text:
            return

        # Show typing indicator
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        try:
            response = self.agent.process_message(user_text)
            await update.message.reply_text(response)
        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)
            await update.message.reply_text("Sorry, I encountered an issue processing that expense.")

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Download receipt image and run multimodal extraction."""
        photos = update.message.photo
        if not photos:
            return

        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        # Get highest resolution photo
        highest_res = photos[-1]
        file_obj = await highest_res.get_file()

        # Save to temporary download location
        download_dir = settings.receipts_dir / "incoming"
        download_dir.mkdir(parents=True, exist_ok=True)
        target_path = download_dir / f"tg_{highest_res.file_unique_id}.jpg"

        await file_obj.download_to_drive(custom_path=target_path)

        # Process receipt
        result = extract_receipt(str(target_path))
        if result.get("status") == "success":
            reply_msg = result.get("confirmation_prompt", "Receipt extracted.")
        else:
            reply_msg = f"Could not process receipt: {result.get('message')}"

        await update.message.reply_text(reply_msg)

    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Download voice message, transcribe it, and route to agent loop (PRD US-9)."""
        voice = update.message.voice
        if not voice:
            return

        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        download_dir = settings.data_dir / "voice"
        download_dir.mkdir(parents=True, exist_ok=True)
        target_path = download_dir / f"voice_{voice.file_unique_id}.ogg"

        file_obj = await voice.get_file()
        await file_obj.download_to_drive(custom_path=target_path)

        try:
            transcription = transcribe_audio_file(target_path)
            # Route text to agent
            response = self.agent.process_message(transcription)
            await update.message.reply_text(f"🎤 <i>\"{transcription}\"</i>\n\n{response}", parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error handling voice message: {e}", exc_info=True)
            await update.message.reply_text("Could not transcribe or process your voice message.")

    def run(self):
        """Start Telegram bot with polling."""
        if not self.token or self.token == "your_telegram_bot_token_here":
            print("ERROR: TELEGRAM_BOT_TOKEN is not configured in .env or settings.")
            print("Please add your token or run in CLI mode using: python main.py --mode cli")
            return

        app = ApplicationBuilder().token(self.token).build()

        app.add_handler(CommandHandler("start", self.start_command))
        app.add_handler(CommandHandler("help", self.help_command))
        app.add_handler(CommandHandler("summary", self.summary_command))
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), self.handle_text))
        app.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        app.add_handler(MessageHandler(filters.VOICE, self.handle_voice))

        print("Telegram Bot is running. Press Ctrl+C to stop.")
        app.run_polling()

