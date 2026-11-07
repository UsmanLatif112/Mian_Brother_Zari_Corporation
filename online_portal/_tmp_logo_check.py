from portal import create_app
from portal.extensions import db
from sqlalchemy import text

app = create_app()
with app.app_context():
    rows = db.session.execute(
        text(
            "SELECT id, name, logo_path, LENGTH(logo_data) AS n, updated_at "
            "FROM portal_companies ORDER BY id"
        )
    ).fetchall()
    for r in rows:
        print(dict(r._mapping))
    t = db.session.execute(
        text("SHOW COLUMNS FROM portal_companies LIKE 'logo_data'")
    ).fetchone()
    print("col", dict(t._mapping) if t else None)

    # Reproduce route for first company with logo
    client = app.test_client()
    for r in rows:
        cid = r._mapping["id"]
        resp = client.get(f"/media/company-logo/{cid}?strict=1")
        print(
            "route",
            cid,
            resp.status_code,
            resp.content_type,
            len(resp.data or b""),
            resp.headers.get("Location"),
        )
