from db import get_db_connection


def main(limit: int = 20) -> None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT hs.id, p.name, p.institution_name, hs.signal_type,
                       hs.confidence, hs.raw_text, hs.source_url, hs.last_checked_at
                FROM hiring_signals hs
                JOIN professors p ON p.id = hs.professor_id
                ORDER BY hs.created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            for row in cursor.fetchall():
                print(
                    f"#{row['id']} {row['name']} ({row['institution_name']})\n"
                    f"  {row['signal_type']} / {row['confidence']}\n"
                    f"  {row['raw_text']}\n"
                    f"  {row['source_url']}\n"
                )


if __name__ == "__main__":
    main()

