from portal import create_app
from portal.extensions import db
from sqlalchemy import text

app = create_app()
with app.app_context():
    users = db.session.execute(
        text(
            """
            SELECT id, username, full_name, company_id, role, is_active_user
            FROM portal_users
            WHERE LOWER(username) LIKE '%saeed%'
               OR LOWER(full_name) LIKE '%saeed%'
               OR LOWER(full_name) LIKE '%hanif%'
            """
        )
    ).fetchall()
    print("portal_users:")
    for r in users:
        print(" ", dict(r._mapping))

    companies = db.session.execute(
        text("SELECT id, name, sync_code FROM portal_companies ORDER BY id")
    ).fetchall()
    print("companies:")
    for r in companies:
        print(" ", dict(r._mapping))

    tables = db.session.execute(text("SHOW TABLES")).fetchall()
    names = [list(r._mapping.values())[0] for r in tables]
    print("tables:", names)

    # Look for company_id / username columns of interest
    for t in names:
        cols = [
            c._mapping["Field"]
            for c in db.session.execute(text(f"SHOW COLUMNS FROM `{t}`")).fetchall()
        ]
        interesting = [
            c
            for c in cols
            if c.lower()
            in {
                "company_id",
                "username",
                "user_id",
                "portal_company_id",
                "sync_code",
                "party_type",
            }
        ]
        if interesting:
            print(f"  {t}: {interesting}")
