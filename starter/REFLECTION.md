# Customer Support Agent Reflection

## Design Decision

I connected each support capability to a dedicated AWS service. Product questions use the Knowledge Base, calculations use Code Interpreter, website requests use AgentCore Browser, and order operations use AgentCore Gateway.

```python
agent_core_browser = AgentCoreBrowser(region=REGION)

tools = [
    search_knowledge_base,
    calculate_loyalty_discount,
    agent_core_browser.browser,
]

mcp_client = MCPClient(
    lambda: streamable_http_client(GATEWAY_URL)
)

with mcp_client:
    gateway_tools = mcp_client.list_tools_sync()
    tools.extend(gateway_tools)
```

This structure helped me understand how an agent chooses between tools. It also reduces guessing. When a customer asks about ORD-001, the agent calls the Gateway because the answer depends on current data. When asked about Platinum benefits, it searches the Knowledge Base. The system prompt directs this behavior and prohibits invented results.

The separation let me test one capability at a time. I confirmed that the order REST API returned a SHIPPED status and UPS tracking number, then tested the same request through AgentCore Gateway. Since the API already worked, a later failure was easier to narrow down to the Gateway or MCP connection. I used the same approach for retrieval, browsing, and calculations.

```python
system_prompt = """
Use the knowledge base for product details, policies, warranties, and loyalty
benefits. Use Gateway tools for order and refund operations, the loyalty tool
for calculations, and the browser only when live web information is requested.
Never invent tool results.
"""
```

## Challenge Encountered

A major challenge was configuration and redeployment. I forgot some environment variables and later forgot that local changes to main.py do not automatically update the cloud runtime. I did not recognize the problem until the deployed agent responded that the customer support service was temporarily unavailable. At first, I thought the agent itself had gone down. I checked the configuration, corrected the Gateway URL and resource IDs, redeployed, and reran all six tests.

I also learned that the API Gateway REST URL and AgentCore Gateway MCP URL serve different purposes. The REST endpoint worked when called directly, but the agent needed the MCP URL ending in `/mcp`. During the browser test, the tool was visible but could not open the website because the runtime role lacked browser-session permission. Adding the permission and testing again resolved it. These problems showed me that successful deployment does not guarantee that every integration is configured correctly.

## Production Considerations

For production, I would prioritize security, monitoring, and cost control. The educational Gateway uses no authorization, but a real system should authenticate customers and verify order ownership before showing details or issuing refunds. Refunds should require confirmation and idempotency keys to prevent duplicates. CloudWatch should track tool failures, latency, token use, and fallback events while keeping customer information out of logs. I would monitor OpenSearch, browser, model, and Code Interpreter costs separately and create alerts for unexpected usage. Memory would need retention and deletion policies. Finally, integration tests and Pydantic validation could catch malformed responses before they reach customers.
