"""
Customer Support AI Agent — Starter Code
==========================================
Your task is to complete this file by implementing all sections marked
with # TODO comments.

Reference the step-by-step solution files and INSTRUCTIONS.md for guidance.
Do NOT copy the solution directly — work through each section yourself.

Run locally (after filling in config values):
  uv run main.py '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'

Deploy to AgentCore:
  agentcore deploy

Invoke deployed agent:
  agentcore invoke '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'
"""

# ── Imports ───────────────────────────────────────────────────────────────────
# These imports are provided. Do not remove them.
from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory import MemoryClient
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamable_http_client
import argparse, json
import os, asyncio, boto3
from strands.hooks import (
    HookProvider, AfterInvocationEvent, HookRegistry, MessageAddedEvent,
)
import logging
import uuid
from typing import Dict
from bedrock_agentcore.tools.code_interpreter_client import code_session
from strands_tools.browser import AgentCoreBrowser

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("CSAI_Agent")

# ── TODO 1 — App Initialisation ───────────────────────────────────────────────
# Create a BedrockAgentCoreApp instance.
# This registers the ASGI server for AgentCore deployment.
# There must be exactly one instance per deployment.
#
# Hint: app = BedrockAgentCoreApp()

# Create the BedrockAgentCoreApp instance
app = BedrockAgentCoreApp()


# Suppress interactive tool-consent prompts (required in headless deployments).
os.environ["BYPASS_TOOL_CONSENT"] = "true"


# ── TODO 2 — Configuration ────────────────────────────────────────────────────
# Replace the placeholder strings with your actual AWS resource values.
# You collected these in Part 1 of the INSTRUCTIONS.
#
# GATEWAY_URL format: https://<alias>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp
# KB_ID       format: 10-character alphanumeric string from the KB console
# REGION:     your AWS region, e.g. "us-east-1"
# MEMORY_ID   format: shown in the AgentCore Memory console

GATEWAY_URL = os.environ.get("GATEWAY_URL", "")  # TODO: Replace with your Gateway URL
KB_ID       = os.environ.get("KB_ID", "")        # TODO: Replace with your Knowledge Base ID
REGION      = "us-east-1"                         # TODO: Replace with your AWS region
MEMORY_ID   = os.environ.get("MEMORY_ID", "")    # TODO: Replace with your Memory ID


# ── TODO 3 — Model and Clients ────────────────────────────────────────────────
# Create:
#   1. A BedrockModel using model_id "global.amazon.nova-2-lite-v1:0"
#   2. A MemoryClient with region_name=REGION
#   3. A boto3 client for the "bedrock-agent-runtime" service in REGION
#
# Hint: model = BedrockModel(model_id=model_id)

model_id = "global.amazon.nova-2-lite-v1:0"

# Create the BedrockModel instance
model = BedrockModel(model_id=model_id, region_name=REGION)

# Create the MemoryClient instance
memory_client = MemoryClient(region_name=REGION)

# Create the boto3 bedrock-agent-runtime client
_bedrock_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)


# ── TODO 4 — Namespace Helper ─────────────────────────────────────────────────
# Implement get_namespaces() to return a dict mapping strategy type to
# namespace template string.
#
# Steps:
#   1. Call mem_client.get_memory_strategies(memory_id) to get strategy list
#   2. Return a dict: { strategy["type"]: strategy["namespaces"][0] for each strategy }
#
# Example output:
#   { "SEMANTIC": "cs_agent/{actorId}/facts",
#     "USER_PREFERENCE": "cs_agent/{actorId}/preferences" }

def get_namespaces(mem_client: MemoryClient, memory_id: str) -> Dict:
    """Return a dict mapping strategy type → namespace template string."""
    # TODO: Implement this function
    if not memory_id:
        return {}

    strategies = mem_client.get_memory_strategies(memory_id)
    namespaces = {}

    for strategy in strategies:
        strategy_type = strategy.get("type")
        templates = (
            strategy.get("namespaceTemplates")
            or strategy.get("namespaces")
            or []
        )

        if strategy_type and templates:
            namespaces[strategy_type] = templates[0]

    return namespaces


# ── TODO 5 — Memory Hook ──────────────────────────────────────────────────────
# Implement MemoryHook, a HookProvider subclass that adds long-term memory.
#
# The class needs:
#   __init__(self, actor_id, session_id, memory_client, memory_id)
#     — store all four as instance attributes
#     — call get_namespaces() and store the result as self.namespaces
#
#   retrieve_customer_context(self, event: MessageAddedEvent)
#     — only runs for plain-text user messages (not tool results)
#     — for each strategy namespace, call memory_client.retrieve_memories(
#          memory_id, namespace (formatted with actorId), query, top_k=5)
#     — collect non-empty memory texts tagged with their strategy type
#     — if any memories found, prepend them to the user message as:
#          "Customer Context:\n<memories>\n\n<original_message>"
#
#   save_support_interaction(self, event: AfterInvocationEvent)
#     — walk the message list backwards to find the last plain-text user
#       query and the last assistant response
#     — call memory_client.create_event(memory_id, actor_id, session_id,
#          messages=[(customer_query, "USER"), (agent_response, "ASSISTANT")])
#
#   register_hooks(self, registry: HookRegistry)
#     — register retrieve_customer_context on MessageAddedEvent
#     — register save_support_interaction on AfterInvocationEvent

class MemoryHook(HookProvider):
    """Long-term memory hook for the customer support agent."""

    def __init__(
        self,
        actor_id: str,
        session_id: str,
        memory_client: MemoryClient,
        memory_id: str,
    ):
        # TODO: Store actor_id, session_id, memory_id, memory_client as attributes
        # TODO: Call get_namespaces() and store the result as self.namespaces
        self.actor_id = actor_id
        self.session_id = session_id
        self.memory_id = memory_id
        self.memory_client = memory_client
        self.namespaces = get_namespaces(memory_client, memory_id)

    def retrieve_customer_context(self, event: MessageAddedEvent):
        """Retrieve relevant memories and prepend them to the user message."""
        # TODO: Implement memory retrieval
        # Steps:
        #   1. Get the last message from event.agent.messages
        #   2. Check it is a user message and not a tool result
        #   3. Extract the user query text
        #   4. For each namespace in self.namespaces, call retrieve_memories()
        #   5. Collect non-empty memory texts with strategy type tags
        #   6. If any found, prepend them to the user message
        messages = event.agent.messages
        if not messages:
            return

        message = messages[-1]
        content = message.get("content", [])
        if message.get("role") != "user" or not content:
            return

        # Tool results are also represented as user messages, so explicitly
        # exclude them before attempting to retrieve conversational memories.
        if any("toolResult" in block for block in content):
            return

        text_blocks = [block.get("text", "") for block in content if "text" in block]
        query = "\n".join(text for text in text_blocks if text).strip()
        if not query:
            return

        customer_memories = []
        for strategy_type, namespace_template in self.namespaces.items():
            namespace = namespace_template.format(actorId=self.actor_id)
            try:
                results = self.memory_client.retrieve_memories(
                    self.memory_id,
                    namespace,
                    query,
                    top_k=5,
                )
            except Exception as exc:
                logger.warning(
                    "Unable to retrieve %s memories for %s: %s",
                    strategy_type,
                    self.actor_id,
                    exc,
                )
                continue

            for memory in results or []:
                memory_text = memory.get("content", {}).get("text", "").strip()
                if memory_text:
                    customer_memories.append(f"[{strategy_type}] {memory_text}")

        if customer_memories:
            context = "\n".join(customer_memories)
            message["content"] = [
                {"text": f"Customer Context:\n{context}\n\n{query}"}
            ]

    def save_support_interaction(self, event: AfterInvocationEvent):
        """Save the completed turn to memory after the agent responds."""
        # TODO: Implement memory saving
        # Steps:
        #   1. Get messages from event.agent.messages
        #   2. Walk backwards to find the last user query (plain text)
        #      and the last assistant response
        #   3. Call memory_client.create_event() with both messages
        customer_query = None
        agent_response = None

        for message in reversed(event.agent.messages):
            content = message.get("content", [])
            if not content or any("toolResult" in block for block in content):
                continue

            text = "\n".join(
                block.get("text", "") for block in content if "text" in block
            ).strip()
            if not text:
                continue

            if message.get("role") == "assistant" and agent_response is None:
                agent_response = text
            elif message.get("role") == "user" and customer_query is None:
                # Remove context injected by retrieve_customer_context before
                # persisting the customer's original message.
                customer_query = text.split("\n\n", 1)[-1]

            if customer_query and agent_response:
                break

        if customer_query and agent_response and self.memory_id:
            try:
                self.memory_client.create_event(
                    self.memory_id,
                    self.actor_id,
                    self.session_id,
                    messages=[
                        (customer_query, "USER"),
                        (agent_response, "ASSISTANT"),
                    ],
                )
            except Exception as exc:
                logger.warning(
                    "Unable to save support interaction for %s: %s",
                    self.actor_id,
                    exc,
                )

    def register_hooks(self, registry: HookRegistry) -> None:  # type: ignore
        """Register both memory callbacks."""
        # TODO: Register retrieve_customer_context on MessageAddedEvent
        # TODO: Register save_support_interaction on AfterInvocationEvent
        registry.add_callback(MessageAddedEvent, self.retrieve_customer_context)
        registry.add_callback(AfterInvocationEvent, self.save_support_interaction)


# ── TODO 6 — Knowledge Base Tool ─────────────────────────────────────────────
# Implement search_knowledge_base(query) using the @tool decorator.
#
# Steps:
#   1. Guard: if KB_ID is empty return "Knowledge base not configured."
#   2. Call _bedrock_runtime.retrieve(
#          knowledgeBaseId=KB_ID,
#          retrievalQuery={"text": query}
#      )
#   3. Extract resp["retrievalResults"]; return a message if empty
#   4. Join the text chunks with "\n---\n" and return the result
#
# The docstring is the tool description — the model uses it to decide when
# to call this tool, so keep it clear and accurate.

@tool
def search_knowledge_base(query: str) -> str:
    """
    Search the Amazon product catalog and support knowledge base.
    Use this for product specifications, return policies, warranty
    information, loyalty program details, and order status definitions.

    Args:
        query: The question or topic to search for

    Returns:
        Relevant information retrieved from the knowledge base
    """
    # TODO: Implement the Knowledge Base search
    if not KB_ID:
        return "Knowledge base not configured."

    try:
        response = _bedrock_runtime.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={"text": query},
        )
        results = response.get("retrievalResults", [])
        if not results:
            return "No relevant information was found in the knowledge base."

        chunks = [
            result.get("content", {}).get("text", "").strip()
            for result in results
        ]
        chunks = [chunk for chunk in chunks if chunk]
        if not chunks:
            return "No relevant information was found in the knowledge base."
        return "\n---\n".join(chunks)
    except Exception as exc:
        logger.exception("Knowledge base retrieval failed")
        return f"Knowledge base search is temporarily unavailable: {exc}"


# ── TODO 7 — Loyalty Discount Tool (Code Interpreter) ────────────────────────
# Implement calculate_loyalty_discount() using the @tool decorator.
#
# The tool must:
#   1. Build a self-contained Python code string that:
#        • Defines earn_rates: {"standard": 1, "device": 2, "fresh": 5}
#        • Defines tier_rates: {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}
#        • Calculates points_redeemed (floor to nearest 500, cap at 50% of order)
#        • Calculates tier_discount (applied to subtotal after points)
#        • Calculates final_total, total_savings, points_earned, remaining_points
#        • Prints a JSON result dict
#   2. Execute the code with code_session(REGION).invoke("executeCode", {...})
#      using language="python" and clearContext=True
#   3. Return the first result event as a JSON string
#   4. Include a fallback that computes only the tier discount if the
#      Code Interpreter is unavailable

@tool
def calculate_loyalty_discount(
    loyalty_points: int,
    tier: str,
    order_total: float,
    product_category: str = "standard",
) -> str:
    """
    Calculate the loyalty discount for a customer order using the
    AgentCore Code Interpreter. Runs exact arithmetic in a secure sandbox.

    Args:
        loyalty_points:   Customer's current points balance
        tier:             Customer tier — Silver, Gold, or Platinum
        order_total:      Order total in USD
        product_category: standard, device, or fresh

    Returns:
        Full discount breakdown and final price
    """
    # TODO: Build the code string (use an f-string to inject the arguments)
    code = f"""


loyalty_points = {loyalty_points!r}
tier = {tier!r}
order_total = {order_total!r}
product_category = {product_category!r}

earn_rates = {{"standard": 1, "device": 2, "fresh": 5}}
tier_rates = {{"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}}

# Points are worth one cent each, may be redeemed in blocks of 500,
# and may cover no more than half of the original order total.
available_blocks = max(0, loyalty_points) // 500
maximum_order_points = int(max(0, order_total) * 0.50 * 100)
maximum_order_blocks = maximum_order_points // 500
points_redeemed = min(available_blocks, maximum_order_blocks) * 500

points_discount = points_redeemed / 100
subtotal_after_points = max(0, order_total - points_discount)
tier_rate = tier_rates.get(tier.title(), 0.00)
tier_discount = subtotal_after_points * tier_rate
final_total = max(0, subtotal_after_points - tier_discount)
total_savings = order_total - final_total
points_earned = int(final_total * earn_rates.get(product_category.lower(), 1))
remaining_points = max(0, loyalty_points - points_redeemed)

result = {{
    "points_redeemed": points_redeemed,
    "points_discount": round(points_discount, 2),
    "tier_discount_rate": tier_rate,
    "tier_discount_pct": round(tier_rate * 100, 2),
    "tier_discount": round(tier_discount, 2),
    "final_total": round(final_total, 2),
    "total_savings": round(total_savings, 2),
    "points_earned": points_earned,
    "remaining_points": remaining_points,
}}
print(json.dumps(result))
"""  # Replace with your code string

    try:
        # TODO: Execute the code using code_session and return the result
        with code_session(REGION) as client:
            response = client.invoke(
                "executeCode",
                {
                    "code": code,
                    "language": "python",
                    "clearContext": True,
                },
            )

            for event in response.get("stream", []):
                if "result" in event:
                    return json.dumps(event["result"])

        raise RuntimeError("Code Interpreter returned no result event")

    except Exception as e:
        # TODO: Implement fallback calculation using tier discount only
        logger.warning("Code Interpreter unavailable; using fallback: %s", e)
        tier_rates = {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}
        tier_rate = tier_rates.get(tier.title(), 0.00)
        tier_discount = max(0, order_total) * tier_rate
        fallback_result = {
            "points_redeemed": 0,
            "tier_discount_rate": tier_rate,
            "tier_discount_pct": round(tier_rate * 100, 2),
            "tier_discount": round(tier_discount, 2),
            "final_total": round(max(0, order_total) - tier_discount, 2),
            "remaining_points": max(0, loyalty_points),
            "calculation_mode": "tier_discount_fallback",
        }
        return json.dumps(fallback_result)


# ── TODO 8 — Agent Entrypoint ─────────────────────────────────────────────────
# Implement the invoke() function decorated with @app.entrypoint.
#
# Steps:
#   1. Extract user_input, actor_id, and session_id from the payload
#      (generate a UUID if session_id is missing)
#   2. Instantiate MemoryHook for this actor/session
#   3. Instantiate AgentCoreBrowser(region=REGION)
#   4. Build the tools list: [search_knowledge_base, calculate_loyalty_discount,
#                              agent_core_browser.browser]
#   5. Connect to the Gateway via MCPClient, load gateway_tools, extend tools list
#   6. Create and invoke the Agent with all tools, hooks, and system_prompt
#   7. Return the text from the first content block of the response
#   8. Handle exceptions gracefully

@app.entrypoint
async def invoke(payload, context=None):
    """
    Main handler called by AgentCore for every incoming request.

    Expected payload keys:
      prompt      (str, required) — the customer's message
      customer_id (str, optional) — unique customer identifier
      session_id  (str, optional) — session identifier; generated if absent
    """
    # TODO: Implement the agent invocation
    user_input = str(payload.get("prompt", "")).strip()
    actor_id = str(payload.get("customer_id", "anonymous"))
    session_id = str(payload.get("session_id") or uuid.uuid4())

    if not user_input:
        return "Please provide a prompt so I can assist you."

    try:
        memory_hook = MemoryHook(
            actor_id,
            session_id,
            memory_client,
            MEMORY_ID,
        )
        agent_core_browser = AgentCoreBrowser(region=REGION)
        tools = [
            search_knowledge_base,
            calculate_loyalty_discount,
            agent_core_browser.browser,
        ]

        system_prompt = """
You are a helpful and concise e-commerce customer support assistant.
Use the knowledge base for product details, policies, warranties, and loyalty
benefits. Use Gateway tools for order and refund operations, the loyalty tool
for calculations, and the browser only when live web information is requested.
Never invent tool results. Ask for any required identifier that is missing.
Use retrieved customer context to personalize responses without exposing the
internal Customer Context block. Clearly summarize outcomes and next steps.
Always answer polite and professional.
"""

        if not GATEWAY_URL:
            raise ValueError(
                "GATEWAY_URL is not configured. Set it before invoking the agent."
            )

        mcp_client = MCPClient(
            lambda: streamable_http_client(GATEWAY_URL)
        )
        with mcp_client:
            gateway_tools = mcp_client.list_tools_sync()
            tools.extend(gateway_tools)
            agent = Agent(
                model=model,
                tools=tools,
                hooks=[memory_hook],
                system_prompt=system_prompt,
            )
            response = agent(user_input)

        return response.message["content"][0]["text"]
    except Exception as exc:
        logger.exception("Customer support invocation failed")
        return (
            "I’m sorry, the customer support service is temporarily unavailable. "
            f"Please try again later. ({type(exc).__name__})"
        )

# ── CLI entry point (do not modify) ──────────────────────────────────────────
def main():
    """Run one invocation from the command line for local testing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=str)
    args = parser.parse_args()
    response = asyncio.run(invoke(json.loads(args.payload)))
    print(response)


if __name__ == "__main__":
    app.run()
    # Uncomment the line below and comment app.run() for local CLI testing:
    # main()
