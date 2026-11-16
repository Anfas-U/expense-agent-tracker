"""Core domain schemas and Pydantic models for the Expense Tracker Agent."""

import datetime as dt
from enum import Enum
from typing import Dict, List, Optional
import uuid
from pydantic import BaseModel, Field


class ExpenseSource(str, Enum):
    """Source channel of the logged expense."""
    TEXT = "text"
    RECEIPT = "receipt"
    VOICE = "voice"
    RECURRING = "recurring"


class PaymentMethod(str, Enum):
    """Common payment methods."""
    CASH = "Cash"
    UPI = "UPI"
    CARD = "Card"
    NET_BANKING = "Net Banking"
    OTHER = "Other"


class CategoryInfo(BaseModel):
    """Definition of an expense category."""
    name: str
    description: str
    keywords: List[str] = Field(default_factory=list)


class ParsedExpenseItem(BaseModel):
    """A single expense item parsed from user input (text or speech)."""
    amount: float = Field(..., gt=0, description="Amount spent")
    currency: str = Field(default="INR", description="ISO currency code")
    merchant: str = Field(..., description="Merchant name or short description")
    category: Optional[str] = Field(default=None, description="Suggested or assigned category")
    date: dt.date = Field(default_factory=dt.date.today, description="Date of transaction")
    payment_method: Optional[str] = Field(default=None, description="Detected payment method")
    notes: Optional[str] = Field(default=None, description="Optional free-form notes")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score 0.0 to 1.0")


class ExpenseEntry(BaseModel):
    """The canonical record format matching the Google Sheets schema (PRD Section 6.1)."""
    id: str = Field(
        default_factory=lambda: f"exp_{dt.datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}",
        description="Unique identifier",
    )
    date: dt.date = Field(default_factory=dt.date.today, description="Date of expense (YYYY-MM-DD)")
    amount: float = Field(..., gt=0, description="Monetary amount spent")
    currency: str = Field(default="INR", description="ISO currency code")
    merchant: str = Field(..., description="Merchant name or description")
    category: str = Field(default="Other", description="Assigned category")
    payment_method: Optional[str] = Field(default=None, description="Payment method (Cash, UPI, Card, etc.)")
    notes: Optional[str] = Field(default=None, description="Additional context or notes")
    source: ExpenseSource = Field(default=ExpenseSource.TEXT, description="Input source channel")
    receipt_url: Optional[str] = Field(default=None, description="Local or remote link to receipt image")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Model confidence score")
    created_at: dt.datetime = Field(
        default_factory=lambda: dt.datetime.now(dt.timezone.utc),
        description="Timestamp logged by agent",
    )

    def to_sheet_row(self) -> List:
        """Serialize entry into a row array matching the Expenses Sheet columns."""
        return [
            self.id,
            self.date.isoformat(),
            self.amount,
            self.currency,
            self.merchant,
            self.category,
            self.payment_method or "",
            self.notes or "",
            self.source.value,
            self.receipt_url or "",
            round(self.confidence, 2),
            self.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        ]

    @classmethod
    def sheet_columns(cls) -> List[str]:
        """Headers matching the Expenses Sheet."""
        return [
            "id",
            "date",
            "amount",
            "currency",
            "merchant",
            "category",
            "payment_method",
            "notes",
            "source",
            "receipt_url",
            "confidence",
            "created_at",
        ]


class ReceiptLineItem(BaseModel):
    """Individual line item detected on a receipt."""
    description: str
    amount: Optional[float] = None
    quantity: Optional[float] = 1.0


class ReceiptExtractedData(BaseModel):
    """Structured fields extracted from an image receipt (PRD Section 4.1 FR-2)."""
    merchant: str = Field(..., description="Store or merchant name")
    date: Optional[dt.date] = Field(default=None, description="Transaction date")
    total_amount: float = Field(..., gt=0, description="Total amount printed on receipt")
    currency: str = Field(default="INR", description="Currency code")
    line_items: List[ReceiptLineItem] = Field(default_factory=list)
    category_suggestion: Optional[str] = Field(default=None)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class ExpenseQueryFilter(BaseModel):
    """Filter parameters for querying expense history."""
    start_date: Optional[dt.date] = None
    end_date: Optional[dt.date] = None
    category: Optional[str] = None
    merchant: Optional[str] = None
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None


class ExpenseQueryResult(BaseModel):
    """Deterministic mathematical output from query_expenses tool (PRD Section 4.5 FR-20)."""
    total_amount: float = 0.0
    entry_count: int = 0
    currency: str = "INR"
    start_date: Optional[dt.date] = None
    end_date: Optional[dt.date] = None
    category_breakdown: Dict[str, float] = Field(default_factory=dict)
    entries: List[ExpenseEntry] = Field(default_factory=list)
    previous_period_total: Optional[float] = None
    percentage_change: Optional[float] = None


class UserPreferences(BaseModel):
    """User settings stored in local database (PRD Section 4.6 FR-23)."""
    user_id: str = "default_user"
    currency: str = "INR"
    timezone: str = "Asia/Kolkata"
    monthly_budgets: Dict[str, float] = Field(default_factory=dict)
    merchant_rules: Dict[str, str] = Field(default_factory=dict)
