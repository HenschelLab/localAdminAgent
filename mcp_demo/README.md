# Local MCP demo: Ollama + Gemma 4

Ask one terminal question and watch a Python host choose, validate, and call MCP
tools. A product lookup can supply the price for a later multiplication.

## Setup and run

Requires Python **3.11+**, Ollama, and enough memory for Gemma 4.
From `mcp_demo`:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
ollama pull gemma4
```

Start the Ollama app, or run `ollama serve` in another terminal. Then:

```sh
.venv/bin/python host.py
```

Enter one question at the prompt:

```text
Question: What do three notebooks cost?
```

Other examples: `Multiply 6 by 7.`, `What is the unit price of a notebook?`,
or `Hello!`. Run the program again for another question.

The settings are fixed in `host.py`: `MODEL = "gemma4:latest"`,
`OLLAMA_HOST = "http://127.0.0.1:11434"`, and `MAX_CALLS = 4`.
The host starts and stops the MCP server automatically.

You can also call it from Python in `mcp_demo`, using the same virtual environment:

```python
import asyncio
from host import run

asyncio.run(run("What do three notebooks cost?"))
```

## Read the code

| Where | Responsibility |
| --- | --- |
| `host.py`: `generate(stage, context, schema=None)` | Make one Ollama model request. |
| `host.py`: `run_workflow(client, generate, question)` | Run the decision stages, validate inputs, and record results. |
| `host.py`: `run(question)` | Open the MCP subprocess connection and run the workflow. |
| `server.py` | Register MCP capabilities and implement Python/SQLite operations. |
| `test_demo.py` | Check the workflow with mocked decisions and real MCP subprocesses. |

The host talks to Ollama over its HTTP API and to the MCP server over **stdio**.
MCP has two protocol layers: **transport** carries messages, and **data** defines
their meaning. The SDK handles both. The decision workflow belongs to the host.
The server's stdout carries MCP messages; diagnostics go to stderr.

## MCP capabilities

| Feature | Interface | Purpose |
| --- | --- | --- |
| Tool | `multiply(a, b)` | Multiply two numbers. |
| Tool | `lookup_product(name)` | Look up a unit price using exact, case-insensitive, parameterized SQL. |
| Tool | `customer_spend(customer)` | Total a customer's orders: SQL join of `orders` to `products`, quantity × price. |
| Resource | `catalog://schema` | Supply fields, product names, and currencies without price values. |
| Prompt | `answer_question(question)` | Supply a reusable template requiring tool-backed arithmetic and prices. |

The server creates `catalog.sqlite3` beside `server.py`. Its initial products are
notebook (AED 12.50), pen (AED 3.00), and mug (AED 25.00). Later runs preserve edits
and deletions. An unknown product returns `found: false` without a price.

## Follow the stages

Startup discovers the capabilities, reads the resource, and renders the prompt.

1. **TRIAGE:** the model chooses `act` or `abstain`.
2. **SELECT:** a separate model request chooses a discovered tool.
3. **BIND:** another request supplies arguments, omitting unknown values.
4. **VALIDATE:** Python checks JSON, finite numbers, allowed names, required fields,
   types, and unexpected arguments.
5. **EXECUTE:** the client requests execution on the server. The host records the
   result and repeats triage with that information.
6. **FINAL:** the model answers from the recorded results and stop reason.

Decision requests use structured JSON output. The trace shows decisions and actual
MCP calls. For three notebooks, expect `lookup_product("notebook")`, followed by
`multiply(12.5, 3)`, then an answer of **AED 37.50**. A greeting needs no tool call.

At most four tools execute per question. Validation failures and tool errors stop
the chain. Missing information should lead to a clarification request. The trace
lets you check the model's choices: schema validation checks structure, not whether
the selected values are semantically correct.

## Tests

```sh
.venv/bin/python -m unittest -v test_demo
```

Tests run without Ollama. They cover stages, chaining, validation, limits, errors,
cleanup, and real MCP discovery/tool/resource/prompt exchanges over stdio. Database
tests copy the server into a temporary directory to isolate its SQLite file and
check lookup safety and persistence.

## References

- [MCP Python SDK v2](https://py.sdk.modelcontextprotocol.io/)
- [MCP architecture](https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture)
- [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs)
