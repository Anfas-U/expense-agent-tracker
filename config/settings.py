"""Application configuration and environment variables."""

import json
from pathlib import Path
from typing import Dict, List, Optional
try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    from pydantic import Field
except ImportError:
    # Minimal fallback for initial bootstrapping prior to pip install
    from pydantic import BaseModel as BaseSettings, Field  # type: ignore
    SettingsConfigDict = dict  # type: ignore

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Configuration settings loaded from environment and defaults."""
    
    # LLM Settings
    llm_provider: str = Field(default="gemini", description="LLM provider: 'gemini' or 'anthropic'")
    llm_api_key: str = Field(default="", description="API key for selected LLM provider")
    llm_model: str = Field(default="gemini-3.8-flash", description="Model identifier for tool-calling & vision")

    # Telegram Settings
    telegram_bot_token: str = Field(default="", description="Telegram Bot token")

    # Google Sheets Settings
    google_credentials_path: str = Field(default="service_account.json", description="Path to Google credentials file")
    google_sheet_name: str = Field(default="My Expense Tracker", description="Name of the Google Spreadsheet")
    google_sheet_id: Optional[str] = Field(default=None, description="Optional explicit Google Sheet ID")

    # Defaults & Locale
    default_currency: str = Field(default="INR", description="Default ISO currency code")
    default_timezone: str = Field(default="Asia/Kolkata", description="Default user timezone")

    # Hosted multi-user web access
    database_url: str = Field(default="", description="PostgreSQL connection URL for hosted accounts and user data")
    cookie_secure: bool = Field(default=True, description="Set session cookies Secure (disable only for local HTTP tests)")

    # File Paths
    data_dir: Path = Field(default=BASE_DIR / "data")
    receipts_dir: Path = Field(default=BASE_DIR / "data" / "receipts")
    database_path: Path = Field(default=BASE_DIR / "data" / "expense_tracker.db")
    categories_file: Path = Field(default=BASE_DIR / "config" / "categories.json")

    # Safety limits
    max_tool_iterations: int = Field(default=6, description="Max tool iterations in agent loop")
    confidence_threshold: float = Field(default=0.6, description="Confidence score to require user clarification")

    try:
        model_config = SettingsConfigDict(
            env_file=str(BASE_DIR / ".env"),
            env_file_encoding="utf-8",
            extra="ignore",
        )
    except Exception:
        pass

    def load_category_definitions(self) -> Dict:
        """Load category taxonomy and rules from categories.json."""
        if self.categories_file.exists():
            with open(self.categories_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"categories": [], "default_category": "Other", "merchant_rules": {}}


settings = Settings()
