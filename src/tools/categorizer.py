"""Categorization tool implementing the tri-level classification hierarchy."""

import re
from typing import Any, Dict, List, Optional
from config.settings import settings
from src.tools.registry import register_tool
from src.storage.web_context import current_categorizer


class Categorizer:
    """Categorizes expenses using: 1. User rules, 2. Keyword & merchant rules, 3. History, 4. LLM."""

    def __init__(self):
        self.category_definitions = settings.load_category_definitions()
        self.categories: List[Dict[str, Any]] = self.category_definitions.get("categories", [])
        self.merchant_rules: Dict[str, str] = self.category_definitions.get("merchant_rules", {})
        self.default_category: str = self.category_definitions.get("default_category", "Other")
        # In-memory user learned mappings
        self.user_learned_rules: Dict[str, str] = {}

    def add_user_rule(self, merchant: str, category: str):
        """Register a user correction rule for future lookups."""
        self.user_learned_rules[merchant.lower().strip()] = category

    def categorize_item(
        self,
        merchant: str,
        description: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Categorize an expense item using the tri-level hierarchy."""
        search_text = f"{merchant} {description or ''} {notes or ''}".lower().strip()
        merchant_clean = merchant.lower().strip()

        # Level 1: Exact User Learned Corrections
        if merchant_clean in self.user_learned_rules:
            return {
                "category": self.user_learned_rules[merchant_clean],
                "confidence": 1.0,
                "strategy": "user_learned_rule",
                "needs_clarification": False,
            }

        # Level 2: Exact Configured Merchant Rules
        for known_merchant, assigned_cat in self.merchant_rules.items():
            if re.search(r"\b" + re.escape(known_merchant) + r"\b", search_text):
                return {
                    "category": assigned_cat,
                    "confidence": 0.95,
                    "strategy": "merchant_rule",
                    "needs_clarification": False,
                }

        # Level 3: Taxonomy Keyword Matching (with word boundaries)
        for cat in self.categories:
            cat_name = cat["name"]
            keywords = cat.get("keywords", [])
            for kw in keywords:
                if re.search(r"\b" + re.escape(kw) + r"\b", search_text):
                    return {
                        "category": cat_name,
                        "confidence": 0.90,
                        "strategy": "keyword_match",
                        "needs_clarification": False,
                    }

        # Level 4: Category Name Direct Mention
        for cat in self.categories:
            cat_name = cat["name"]
            if re.search(r"\b" + re.escape(cat_name.lower()) + r"\b", search_text):
                return {
                    "category": cat_name,
                    "confidence": 0.90,
                    "strategy": "direct_name_match",
                    "needs_clarification": False,
                }

        # Fallback Level 5: Ambiguous / Low Confidence
        return {
            "category": self.default_category,
            "confidence": 0.50,
            "strategy": "default_fallback",
            "needs_clarification": True,
        }


# Singleton instance
_categorizer_instance = Categorizer()


def get_categorizer() -> Categorizer:
    """Get the active categorizer instance."""
    return current_categorizer() or _categorizer_instance


@register_tool(
    name="categorize",
    description="Assign an expense category to an item based on merchant, description, and keywords.",
)
def categorize(
    merchant: str,
    description: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Categorize an expense item."""
    return get_categorizer().categorize_item(merchant, description, notes)
