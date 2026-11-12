"""A local MCP server exposing arithmetic, a SQLite product catalog, and customer orders."""

from contextlib import closing
from pathlib import Path
import sqlite3

from mcp.server import MCPServer

DB_PATH = Path(__file__).with_name("catalog.sqlite3")
mcp = MCPServer("Arithmetic and product catalog", log_level="WARNING")


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def initialize_db() -> None:
    """Create and seed each table once; existing rows are never overwritten."""
    with closing(sqlite3.connect(DB_PATH)) as connection, connection:
        if not table_exists(connection, "products"):
            connection.execute(
                "CREATE TABLE products (name TEXT PRIMARY KEY, "
                "price REAL NOT NULL, currency TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO products (name, price, currency) VALUES (?, ?, ?)",
                [("notebook", 12.5, "AED"), ("pen", 3.0, "AED"), ("mug", 25.0, "AED")],
            )
        if not table_exists(connection, "orders"):
            connection.execute(
                "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer TEXT NOT NULL, "
                "product TEXT NOT NULL REFERENCES products(name), "
                "quantity INTEGER NOT NULL CHECK (quantity > 0))"
            )
            connection.executemany(
                "INSERT INTO orders (customer, product, quantity) VALUES (?, ?, ?)",
                [("Layla", "notebook", 2), ("Layla", "pen", 10),
                 ("Omar", "mug", 1), ("Omar", "pen", 4)],
            )


@mcp.tool()
def multiply(a: float, b: float) -> float:
    """Multiply two numbers, for example a product's unit price and quantity."""
    return a * b


@mcp.tool()
def lookup_product(name: str) -> dict:
    """Find a product's name, unit price, and currency by exact name, ignoring case."""
    with closing(sqlite3.connect(DB_PATH)) as connection:
        row = connection.execute(
            "SELECT name, price, currency FROM products WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
    if row is None:
        return {"found": False, "name": name}
    return {"found": True, "name": row[0], "price": row[1], "currency": row[2]}


@mcp.tool()
def customer_spend(customer: str) -> dict:
    """Total AED spend for one customer: quantity times unit price, summed in SQL."""
    with closing(sqlite3.connect(DB_PATH)) as connection:
        row = connection.execute(
            """
            SELECT o.customer, ROUND(SUM(o.quantity * p.price), 2)
            FROM orders AS o JOIN products AS p ON p.name = o.product
            WHERE o.customer = ? COLLATE NOCASE
            GROUP BY o.customer
            """,
            (customer,),
        ).fetchone()
    if row is None:
        return {"found": False, "customer": customer}
    return {"found": True, "customer": row[0], "total": row[1], "currency": "AED"}


@mcp.resource("catalog://schema")
def catalog_schema() -> dict:
    """Describe the catalog and available product names without revealing prices."""
    with closing(sqlite3.connect(DB_PATH)) as connection:
        rows = connection.execute("SELECT name, currency FROM products ORDER BY name").fetchall()
    return {
        "table": "products",
        "fields": {"name": "text", "price": "number", "currency": "text"},
        "currencies": sorted({row[1] for row in rows}),
        "product_names": [row[0] for row in rows],
    }


@mcp.prompt()
def answer_question(question: str) -> str:
    """Answer a question using the catalog and arithmetic tools when needed."""
    return (
        "Answer the user's question. Use successful tool results to support arithmetic "
        "and product facts. Look up a product's unit price before using it in a calculation. "
        "Never invent arguments, quantities, prices, or unavailable facts. If a required "
        "input is missing, ask for it. If a product is not found or an operation fails, "
        "explain that limitation. Use the catalog resource for schema, names, and currency "
        "context; it does not contain prices.\n\nUser question:\n" + question
    )


if __name__ == "__main__":
    initialize_db()
    mcp.run(transport="stdio")
