from pathlib import Path

from db import get_db_connection


def main() -> None:
    schema_path = Path(__file__).resolve().parents[1] / "db.sql"
    schema = schema_path.read_text(encoding="utf-8")
    with get_db_connection() as connection:
        connection.execute(schema, prepare=False)
    print(f"Applied {schema_path.name} successfully.")


if __name__ == "__main__":
    main()
