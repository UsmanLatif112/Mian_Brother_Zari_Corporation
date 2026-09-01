from app import create_app

app = create_app()
with app.app_context():
    from app.services.dashboard_service import ensure_schema

    ensure_schema()
    from app.extensions import db
    from sqlalchemy import text

    n = db.session.execute(
        text("SELECT COUNT(*) FROM stock_movements WHERE movement_type='sale_void_in'")
    ).scalar()
    print("sale_void_in movements:", n)
print("app ok")
