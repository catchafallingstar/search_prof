"""Create the single ScholarRadar owner after that email has a user row."""

import argparse
import os

from dotenv import load_dotenv

from db import bootstrap_site_owner, upsert_authenticated_user


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Create or confirm the single site owner.")
    parser.add_argument("--email", default=os.getenv("DEV_USER_EMAIL", ""))
    parser.add_argument("--name", default=os.getenv("DEV_USER_NAME", "Site Owner"))
    parser.add_argument(
        "--create-local-user",
        action=argparse.BooleanOptionalAction,
        default=os.getenv("APP_ENV", "").lower() == "development",
        help="Create the development user row before granting owner authority.",
    )
    args = parser.parse_args()

    email = args.email.strip().lower()
    if not email or "@" not in email:
        raise SystemExit("Provide a real email with --email or DEV_USER_EMAIL.")

    if args.create_local_user:
        upsert_authenticated_user(f"local-dev:{email}", email, args.name.strip() or "Site Owner")

    owner = bootstrap_site_owner(email)
    print(f"Site owner confirmed: {owner['email']} (user id {owner['user_id']})")


if __name__ == "__main__":
    main()
