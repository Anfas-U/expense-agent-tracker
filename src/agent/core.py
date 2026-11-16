"""Pure Python Agent Core Loop and LLM Provider Abstraction.

Implements PRD Section 7.1:
"The loop is implemented by hand in plain Python. Agent frameworks (LangChain,
LlamaIndex, CrewAI, etc.) are not used, so that every step of the model-tool
cycle is visible and testable."
"""

import json
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel

from config.settings import settings
from src.agent.prompts import SYSTEM_PROMPT
from src.tools.registry import execute_tool, get_tool_schemas


class ToolCall(BaseModel):
    """Normalized tool call representation."""
    id: str
    name: str
    arguments: Dict[str, Any]


class AgentMessage(BaseModel):
    """Normalized message representation across providers."""
    role: str  # 'system', 'user', 'assistant', 'tool'
    content: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None  # name of the tool that produced this result
    provider_content: Any = None  # raw provider reply, replayed as-is (keeps thought signatures)


class AgentResponse(BaseModel):
    """Normalized response from LLM client."""
    text: Optional[str] = None
    tool_calls: List[ToolCall] = []
    raw_content: Any = None  # raw provider reply content, kept for replay

    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class BaseLLMClient(ABC):
    """Abstract interface for LLM providers."""

    @abstractmethod
    def generate(
        self,
        messages: List[AgentMessage],
        tool_schemas: List[Dict[str, Any]],
    ) -> AgentResponse:
        """Call the model with conversation history and available tools."""
        pass


class MockLLMClient(BaseLLMClient):
    """Mock LLM client for automated testing and local development without API keys."""

    def __init__(self):
        self.call_history: List[List[AgentMessage]] = []

    def generate(
        self,
        messages: List[AgentMessage],
        tool_schemas: List[Dict[str, Any]],
    ) -> AgentResponse:
        self.call_history.append(messages)
        last_message = messages[-1]

        # If previous message was a tool result, formulate final confirmation
        if last_message.role == "tool":
            try:
                data = json.loads(last_message.content or "{}")
                if isinstance(data, dict):
                    if "formatted_summary" in data:
                        return AgentResponse(text=data["formatted_summary"])
                    if "confirmation_message" in data:
                        return AgentResponse(text=data["confirmation_message"])
                    if "confirmation_prompt" in data:
                        return AgentResponse(text=data["confirmation_prompt"])
                    if "message" in data:
                        return AgentResponse(text=data["message"])

                if isinstance(data, list) and len(data) > 0:
                    items = data
                    from src.tools.spreadsheet import append_row
                    if len(items) == 1:
                        item = items[0]
                        merchant = item.get("merchant", "Expense")
                        cat = item.get("category")
                        if not cat or cat == "Other":
                            from src.tools.categorizer import categorize
                            cat_res = categorize(merchant)
                            cat = cat_res.get("category", "Groceries" if "groceries" in merchant.lower() else "Expense")
                        amt = item.get("amount", 0)
                        append_row(
                            amount=amt,
                            merchant=merchant,
                            category=cat,
                            date_str=item.get("date"),
                            payment_method=item.get("payment_method"),
                            force=True,
                        )
                        return AgentResponse(text=f"Logged ₹{int(amt) if amt == int(amt) else amt}, {cat}, today.")
                    else:
                        for it in items:
                            append_row(
                                amount=it.get("amount", 0),
                                merchant=it.get("merchant", "Expense"),
                                category=it.get("category"),
                                date_str=it.get("date"),
                                payment_method=it.get("payment_method"),
                                force=True,
                            )
                        total = sum(i.get("amount", 0) for i in items)
                        count = len(items)
                        return AgentResponse(text=f"Logged {count} expenses. Total ₹{int(total) if total == int(total) else total}.")
            except Exception:
                pass
            return AgentResponse(text="Expense successfully logged.")

        user_text = (last_message.content or "").lower()

        # Handle common spending questions even when no live LLM key is configured.
        is_expense_query = any(
            phrase in user_text
            for phrase in [
                "how much",
                "what did i spend",
                "what have i spent",
                "what is my total",
                "what's my total",
                "spending this",
                "spending on",
                "total spent",
                "total amount spent",
                "amount spent",
            ]
        )
        if is_expense_query:
            cat = None
            for candidate in ["food", "groceries", "dining", "transport", "fuel", "utilities", "rent", "shopping", "health", "entertainment", "education", "travel", "subscriptions"]:
                if candidate in user_text:
                    cat = candidate
                    break

            period = "this_month"
            if "last month" in user_text:
                period = "last_month"
            elif "this week" in user_text:
                period = "this_week"
            elif "today" in user_text:
                period = "today"
            elif "october" in user_text:
                period = "october"

            return AgentResponse(
                tool_calls=[
                    ToolCall(
                        id="call_mock_query_1",
                        name="query_expenses",
                        arguments={"category": cat, "time_period": period},
                    )
                ]
            )

        # Handle corrections e.g. "change that to 540"
        import re
        match_change = re.search(r"change\s+(?:that\s+)?to\s+([\d,]+(?:\.\d+)?)", user_text)
        if match_change:
            clean_num = float(match_change.group(1).replace(",", ""))
            return AgentResponse(
                tool_calls=[
                    ToolCall(
                        id="call_mock_update_1",
                        name="update_row",
                        arguments={"new_amount": clean_num},
                    )
                ]
            )

        # Handle deletions e.g. "delete the last one"
        if "delete" in user_text and ("last" in user_text or "that" in user_text):
            return AgentResponse(
                tool_calls=[
                    ToolCall(
                        id="call_mock_delete_1",
                        name="delete_row",
                        arguments={},
                    )
                ]
            )

        # If user message mentions logging or spending, call parse_expense tool
        if any(w in user_text for w in ["spent", "paid", "bought", "coffee", "lunch", "auto", "groceries", "₹", "rs"]):
            return AgentResponse(
                tool_calls=[
                    ToolCall(
                        id="call_mock_parse_1",
                        name="parse_expense",
                        arguments={"text": last_message.content},
                    )
                ]
            )

        return AgentResponse(text="How can I help you with your expenses today?")


class GeminiClient(BaseLLMClient):
    """Google Gemini client using google-genai SDK."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        from google import genai
        from google.genai import types
        self.client = genai.Client(api_key=api_key or settings.llm_api_key)
        self.model_name = model or settings.llm_model
        self.types = types

    def generate(
        self,
        messages: List[AgentMessage],
        tool_schemas: List[Dict[str, Any]],
    ) -> AgentResponse:
        # Build contents for Gemini API
        contents = []
        pending_tool_parts = []  # tool results from one turn must be sent together

        def flush_tool_parts():
            if pending_tool_parts:
                contents.append(self.types.Content(role="user", parts=list(pending_tool_parts)))
                pending_tool_parts.clear()

        for msg in messages:
            if msg.role == "tool":
                pending_tool_parts.append(
                    self.types.Part.from_function_response(
                        name=msg.tool_name or "function",
                        response={"result": msg.content},
                    )
                )
                continue

            flush_tool_parts()

            if msg.role == "user":
                contents.append(self.types.Content(role="user", parts=[self.types.Part.from_text(text=msg.content or "")]))
            elif msg.role == "assistant":
                if msg.provider_content is not None:
                    # Replay the model's own reply untouched. Gemini 3.x needs the hidden
                    # "thought_signature" inside it, which rebuilding the parts would lose.
                    contents.append(msg.provider_content)
                else:
                    parts = []
                    if msg.content:
                        parts.append(self.types.Part.from_text(text=msg.content))
                    if msg.tool_calls:
                        for tc in msg.tool_calls:
                            parts.append(
                                self.types.Part.from_function_call(name=tc.name, args=tc.arguments)
                            )
                    contents.append(self.types.Content(role="model", parts=parts))

        flush_tool_parts()

        # Convert tool schemas to Gemini types
        gemini_tools = []
        if tool_schemas:
            func_decls = []
            for s in tool_schemas:
                func_decls.append(
                    self.types.FunctionDeclaration(
                        name=s["name"],
                        description=s["description"],
                        parameters=s["parameters"],
                    )
                )
            gemini_tools.append(self.types.Tool(function_declarations=func_decls))

        response = None
        for attempt in range(4):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=contents,
                    config=self.types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        tools=gemini_tools if gemini_tools else None,
                        temperature=0.2,
                    ),
                )
                break
            except Exception as e:
                # 429 / 503 = Google is busy; wait a little and try again
                if getattr(e, "code", None) in (429, 503) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise

        candidate = response.candidates[0] if response.candidates else None
        raw_content = candidate.content if candidate else None
        parts = (raw_content.parts or []) if raw_content else []

        text_out = "".join(
            p.text for p in parts
            if getattr(p, "text", None) and not getattr(p, "thought", False)
        ) or None

        tool_calls: List[ToolCall] = []
        for p in parts:
            fc = getattr(p, "function_call", None)
            if fc:
                tool_calls.append(
                    ToolCall(
                        id=f"gemini_call_{len(tool_calls)}",
                        name=fc.name,
                        arguments=dict(fc.args) if fc.args else {},
                    )
                )

        return AgentResponse(text=text_out, tool_calls=tool_calls, raw_content=raw_content)


class ExpenseAgent:
    """Core hand-crafted Agent orchestrator."""

    def __init__(self, llm_client: Optional[BaseLLMClient] = None):
        self.llm = llm_client or self._resolve_llm_client()
        self.conversation_history: List[AgentMessage] = [
            AgentMessage(role="system", content=SYSTEM_PROMPT)
        ]

    def _resolve_llm_client(self) -> BaseLLMClient:
        """Resolve LLM client based on configuration."""
        if settings.llm_provider == "gemini" and settings.llm_api_key:
            return GeminiClient()
        # Default to Mock client if no API key is set yet
        return MockLLMClient()

    def process_message(self, user_text: str) -> str:
        """Execute the agent loop for an incoming user text message.

        Implements the PRD 7.1 cycle:
        1. Receive message.
        2. Decide intent / call LLM.
        3. Call tool(s) deterministically.
        4. Confirm result to user.
        """
        # Append incoming message
        self.conversation_history.append(AgentMessage(role="user", content=user_text))

        tool_schemas = get_tool_schemas()
        max_iterations = settings.max_tool_iterations

        for step in range(max_iterations):
            agent_response = self.llm.generate(self.conversation_history, tool_schemas)

            if agent_response.has_tool_calls():
                # Record assistant's tool-calling intent
                self.conversation_history.append(
                    AgentMessage(
                        role="assistant",
                        content=agent_response.text,
                        tool_calls=agent_response.tool_calls,
                        provider_content=agent_response.raw_content,
                    )
                )

                # Execute each requested tool
                for tool_call in agent_response.tool_calls:
                    try:
                        tool_output = execute_tool(tool_call.name, tool_call.arguments)
                        serialized_output = json.dumps(tool_output, default=str)
                    except Exception as e:
                        serialized_output = json.dumps({"error": str(e)})

                    # Append tool response
                    self.conversation_history.append(
                        AgentMessage(
                            role="tool",
                            content=serialized_output,
                            tool_call_id=tool_call.id,
                            tool_name=tool_call.name,
                        )
                    )
            else:
                # LLM finished and returned a conversational response
                reply_text = agent_response.text or "I have processed your request."
                self.conversation_history.append(AgentMessage(role="assistant", content=reply_text))
                return reply_text

        return "I encountered an issue processing your request within the iteration limit."