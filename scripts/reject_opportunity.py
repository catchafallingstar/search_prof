import argparse

from db import get_db_connection, review_item


def active_owner_id() -> int:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT user_id FROM site_admins
                WHERE admin_role = 'owner' AND revoked_at IS NULL
                """
            )
            owner = cursor.fetchone()
    if not owner:
        raise RuntimeError("No active site owner exists.")
    return int(owner["user_id"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reject one or more pending opportunities with an owner audit record."
    )
    parser.add_argument("opportunity_ids", nargs="+", type=int)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    owner_id = active_owner_id()
    for opportunity_id in args.opportunity_ids:
        review_item("opportunity", opportunity_id, False, owner_id, args.reason)
        print(f"Rejected opportunity {opportunity_id}.")


if __name__ == "__main__":
    main()
