"""Deterministic host tests and real MCP subprocess tests; no Ollama required."""

import copy
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from mcp import Client, StdioServerParameters, types

import host


SERVER = Path(__file__).with_name("server.py").resolve()


def tool_result(value, *, error=False, structured=False):
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(value))],
        structuredContent=value if structured else None,
        isError=error,
    )


def fake_client():
    """Use actual SDK result models so fake transport matches the MCP interface."""
    client = AsyncMock()
    client.list_tools.return_value = types.ListToolsResult(tools=[
        types.Tool(
            name="multiply", description="Multiply two numbers.",
            inputSchema={
                "type": "object",
                "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                "required": ["a", "b"],
            },
        ),
        types.Tool(
            name="lookup_product", description="Look up a product's unit price.",
            inputSchema={
                "type": "object", "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        ),
    ])
    client.list_resources.return_value = types.ListResourcesResult(resources=[
        types.Resource(name="catalog_schema", uri="catalog://schema"),
    ])
    client.list_prompts.return_value = types.ListPromptsResult(prompts=[
        types.Prompt(name="answer_question"),
    ])
    client.read_resource.return_value = types.ReadResourceResult(contents=[
        types.TextResourceContents(uri="catalog://schema", text=json.dumps({
            "fields": {"name": "text", "price": "number", "currency": "text"},
            "currencies": ["AED"], "product_names": ["mug", "notebook", "pen"],
        })),
    ])
    client.get_prompt.return_value = types.GetPromptResult(messages=[
        types.PromptMessage(role="user", content=types.TextContent(
            type="text", text="Use tools for arithmetic and product facts.",
        )),
    ])
    client.call_tool.return_value = tool_result({"result": 42.0}, structured=True)
    return client


class ScriptedModel:
    """Assert stage order and record the context actually supplied to the model."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def generate(self, stage, context, schema):
        self.calls.append((stage, copy.deepcopy(context), copy.deepcopy(schema)))
        if not self.replies:
            raise AssertionError(f"Unexpected model stage: {stage}")
        expected_stage, reply = self.replies.pop(0)
        if stage != expected_stage:
            raise AssertionError(f"Expected {expected_stage}, received {stage}")
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)


def action(name="multiply", arguments=None):
    return [
        ("TRIAGE", {"decision": "act"}),
        ("SELECT", {"tool": name}),
        ("BIND", arguments if arguments is not None else {"a": 6, "b": 7}),
    ]


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.output = StringIO()
        self.enterContext(redirect_stdout(self.output))

    async def test_arithmetic_discovers_capabilities_and_shows_actual_call(self):
        client = fake_client()
        model = ScriptedModel(
            *action(), ("TRIAGE", {"decision": "abstain"}), ("FINAL", "42"),
        )
        result = await host.run_workflow(client, model.generate, "Multiply 6 by 7")
        self.assertEqual(result["answer"], "42")
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(result["observations"][0]["result"], {"result": 42.0})
        self.assertFalse(result["observations"][0]["is_error"])
        client.call_tool.assert_awaited_once_with("multiply", {"a": 6, "b": 7})
        client.list_tools.assert_awaited_once()
        client.list_resources.assert_awaited_once()
        client.list_prompts.assert_awaited_once()
        client.read_resource.assert_awaited_once()
        client.get_prompt.assert_awaited_once()
        output = self.output.getvalue()
        for label in ("TRIAGE", "SELECT", "BIND", "VALIDATE", "TOOL CALL", "TOOL RESULT", "FINAL"):
            self.assertIn(f"[{label}]", output)
        select_schema = model.calls[1][2]
        self.assertEqual(set(select_schema["properties"]["tool"]["enum"]), {"multiply", "lookup_product"})
        bind_schema = model.calls[2][2]
        self.assertFalse(bind_schema.get("required"))
        self.assertFalse(model.replies)

    async def test_chain_passes_lookup_result_to_later_decisions(self):
        client = fake_client()
        product = {"found": True, "name": "notebook", "price": 12.5, "currency": "AED"}
        client.call_tool.side_effect = [
            tool_result(product), tool_result({"result": 37.5}, structured=True),
        ]
        model = ScriptedModel(
            *action("lookup_product", {"name": "notebook"}),
            *action("multiply", {"a": 12.5, "b": 3}),
            ("TRIAGE", {"decision": "abstain"}), ("FINAL", "Three notebooks cost AED 37.50."),
        )
        result = await host.run_workflow(client, model.generate, "What do three notebooks cost?")
        self.assertEqual([call.args for call in client.call_tool.await_args_list], [
            ("lookup_product", {"name": "notebook"}), ("multiply", {"a": 12.5, "b": 3}),
        ])
        self.assertEqual([item["tool"] for item in result["observations"]], ["lookup_product", "multiply"])
        self.assertIn("37.50", result["answer"])
        for stage, context, _ in model.calls[3:]:
            self.assertIn("12.5", json.dumps(context), stage)

    async def test_greeting_abstains_without_calling_tools(self):
        client = fake_client()
        model = ScriptedModel(("TRIAGE", {"decision": "abstain"}), ("FINAL", "Hello!"))
        result = await host.run_workflow(client, model.generate, "Hello")
        self.assertEqual(result["answer"], "Hello!")
        self.assertEqual(result["observations"], [])
        client.call_tool.assert_not_awaited()

    async def test_invalid_model_responses_never_execute_a_tool(self):
        scenarios = {
            "missing required argument": action(arguments={"a": 6}),
            "wrong argument type": action(arguments={"a": "six", "b": 7}),
            "boolean is not a number": action(arguments={"a": True, "b": 7}),
            "unexpected argument": action(arguments={"a": 6, "b": 7, "extra": 9}),
            "malformed arguments": action()[:-1] + [("BIND", "{broken")],
            "nonfinite arguments": action()[:-1] + [("BIND", '{"a": NaN, "b": 7}')],
            "unknown tool": [("TRIAGE", {"decision": "act"}), ("SELECT", {"tool": "delete_database"})],
            "malformed selection": [("TRIAGE", {"decision": "act"}), ("SELECT", "not JSON")],
            "invalid triage": [("TRIAGE", {"decision": "maybe"})],
        }
        for name, replies in scenarios.items():
            with self.subTest(name=name):
                client = fake_client()
                model = ScriptedModel(*replies, ("FINAL", "Unable to complete this request."))
                result = await host.run_workflow(client, model.generate, "Multiply")
                self.assertTrue(result["stop_reason"].startswith("validation failed"), result)
                self.assertEqual(result["observations"], [])
                client.call_tool.assert_not_awaited()
                self.assertFalse(model.replies)

    async def test_unknown_product_is_recorded_without_a_fabricated_price(self):
        client = fake_client()
        client.call_tool.return_value = tool_result({"found": False, "name": "bicycle"})
        model = ScriptedModel(
            *action("lookup_product", {"name": "bicycle"}),
            ("TRIAGE", {"decision": "abstain"}), ("FINAL", "Bicycle was not found in the catalog."),
        )
        result = await host.run_workflow(client, model.generate, "What does a bicycle cost?")
        self.assertEqual(len(result["observations"]), 1)
        self.assertFalse(result["observations"][0]["is_error"])
        self.assertIn('"found": false', json.dumps(result["observations"][0]["result"]))
        self.assertNotIn('"price"', json.dumps(result["observations"][0]["result"]))
        self.assertEqual(client.call_tool.await_count, 1)

    async def test_four_call_limit_blocks_a_fifth_execution(self):
        client = fake_client()
        model = ScriptedModel(
            *(action() * 4), ("TRIAGE", {"decision": "act"}), ("FINAL", "Stopped at the tool limit."),
        )
        result = await host.run_workflow(client, model.generate, "Keep multiplying")
        self.assertEqual(client.call_tool.await_count, 4)
        self.assertEqual(len(result["observations"]), 4)
        self.assertIn("limit", result["stop_reason"])
        self.assertFalse(model.replies)

    async def test_tool_error_or_transport_exception_stops_the_chain(self):
        for failure in (tool_result("Database unavailable", error=True), ConnectionError("MCP connection lost")):
            with self.subTest(failure=repr(failure)):
                client = fake_client()
                if isinstance(failure, Exception):
                    client.call_tool.side_effect = failure
                else:
                    client.call_tool.return_value = failure
                model = ScriptedModel(*action(), ("FINAL", "The tool failed."))
                result = await host.run_workflow(client, model.generate, "Multiply 6 by 7")
                self.assertEqual(client.call_tool.await_count, 1)
                self.assertTrue(result["observations"][0]["is_error"])
                self.assertTrue(result["stop_reason"].startswith("tool error"), result)

    async def test_ollama_connection_failure_closes_mcp_context(self):
        client = fake_client()
        context = AsyncMock()
        context.__aenter__.return_value = client
        context.__aexit__.return_value = False
        with patch.object(host, "Client", return_value=context) as constructor:
            with patch.object(host, "generate", new=AsyncMock(side_effect=ConnectionError("Ollama unavailable"))):
                with self.assertRaisesRegex(ConnectionError, "Ollama unavailable"):
                    await host.run("Hello")
        context.__aenter__.assert_awaited_once()
        context.__aexit__.assert_awaited_once()
        parameters = constructor.call_args.args[0]
        self.assertEqual(parameters.command, sys.executable)
        self.assertEqual(parameters.args, [str(SERVER)])

    async def test_blank_question_does_not_launch_server(self):
        with patch.object(host, "Client") as constructor:
            with self.assertRaises(ValueError):
                await host.run(" \n\t")
        constructor.assert_not_called()

    async def test_generate_uses_fixed_local_model_and_structured_options(self):
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.chat.return_value = SimpleNamespace(message=SimpleNamespace(content='{"decision": "abstain"}'))
        schema = {"type": "object", "properties": {"decision": {"enum": ["act", "abstain"]}}}
        with patch.object(host, "AsyncClient", return_value=client) as constructor:
            response = await host.generate("TRIAGE", {"question": "Hello"}, schema)
        constructor.assert_called_once_with(host="http://127.0.0.1:11434", timeout=120)
        options = client.chat.await_args.kwargs
        self.assertEqual(options["model"], "gemma4:latest")
        self.assertEqual(options["format"], schema)
        self.assertFalse(options["stream"])
        self.assertFalse(options["think"])
        self.assertEqual(options["options"], {"temperature": 0, "num_ctx": 8192})
        self.assertEqual(json.loads(options["messages"][1]["content"]), {"question": "Hello"})
        self.assertEqual(json.loads(response), {"decision": "abstain"})
        client.__aexit__.assert_awaited_once()


class MCPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "catalog.sqlite3"
        self.server = Path(self.temp.name) / "server.py"
        shutil.copy2(SERVER, self.server)

    def client(self):
        return Client(StdioServerParameters(
            command=sys.executable, args=[str(self.server)],
        ))

    @staticmethod
    def payload(result):
        if result.structured_content is not None:
            return result.structured_content
        return json.loads("".join(part.text for part in result.content if part.type == "text"))

    async def test_discovery_resource_prompt_and_arithmetic_over_stdio(self):
        async with self.client() as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            self.assertEqual(set(tools), {"multiply", "lookup_product", "customer_spend"})
            self.assertEqual(set(tools["multiply"].input_schema["required"]), {"a", "b"})
            self.assertIn("catalog://schema", [str(resource.uri) for resource in (await client.list_resources()).resources])
            self.assertIn("answer_question", [prompt.name for prompt in (await client.list_prompts()).prompts])
            resource = await client.read_resource("catalog://schema")
            catalog = json.loads(resource.contents[0].text)
            self.assertEqual(set(catalog["product_names"]), {"notebook", "pen", "mug"})
            self.assertEqual(catalog["currencies"], ["AED"])
            self.assertEqual(set(catalog["fields"]), {"name", "price", "currency"})
            self.assertNotIn("12.5", resource.contents[0].text)
            prompt = await client.get_prompt("answer_question", {"question": "Multiply 6 by 7"})
            self.assertIn("Multiply 6 by 7", prompt.messages[0].content.text)
            result = await client.call_tool("multiply", {"a": 6, "b": 7})
            self.assertFalse(result.is_error)
            self.assertEqual(self.payload(result), {"result": 42.0})

    async def test_catalog_lookup_case_insensitivity_unknown_and_sql_parameterization(self):
        async with self.client() as client:
            for name, price in (("NOTEBOOK", 12.5), ("pen", 3.0), ("mug", 25.0)):
                with self.subTest(name=name):
                    product = self.payload(await client.call_tool("lookup_product", {"name": name}))
                    self.assertEqual(product, {"found": True, "name": name.lower(), "price": price, "currency": "AED"})
            for name in ("bicycle", "' OR 1=1 --"):
                product = self.payload(await client.call_tool("lookup_product", {"name": name}))
                self.assertEqual(product, {"found": False, "name": name})

    async def test_customer_spend_joins_orders_to_prices_in_sql(self):
        async with self.client() as client:
            # Layla: 2 notebooks at 12.5 + 10 pens at 3.0 = 55.0
            spend = self.payload(await client.call_tool("customer_spend", {"customer": "layla"}))
            self.assertEqual(spend, {"found": True, "customer": "Layla", "total": 55.0, "currency": "AED"})
            self.assertFalse(self.payload(await client.call_tool("customer_spend", {"customer": "Nadia"}))["found"])

    async def test_database_changes_survive_server_restart(self):
        async with self.client() as client:
            self.assertEqual(self.payload(await client.call_tool("lookup_product", {"name": "notebook"}))["price"], 12.5)
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE products SET price = ? WHERE name = ?", (15.0, "notebook"))
            connection.execute("DELETE FROM products WHERE name = ?", ("pen",))
        async with self.client() as client:
            self.assertEqual(self.payload(await client.call_tool("lookup_product", {"name": "notebook"}))["price"], 15.0)
            self.assertFalse(self.payload(await client.call_tool("lookup_product", {"name": "pen"}))["found"])


if __name__ == "__main__":
    unittest.main()
