# MBZC ERP — Fertilizer & Agricultural Business Management

Production-oriented Flask ERP with **offline-first SQLite**, **push sync to MySQL**, **FIFO inventory**, role-based auth, and a modern green admin UI.

## Quick start (offline)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
flask --app run init-db
python run.py
```

Open http://127.0.0.1:5000 — login: **admin** / **admin123**

Default settings (`OFFLINE_FIRST=true`, `DATABASE_MODE=sqlite`): all work is stored in **local SQLite**. No internet required.

## Sync to MySQL (when online)

1. Set `MYSQL_DATABASE_URI` (and optionally `MYSQL_TEST_DATABASE_URI`) in `.env`.
2. Choose target: `SYNC_MYSQL_TARGET=production` or `test`.
3. Open **Sync** in the app → **Push local data to MySQL**.
4. Remote tables are created automatically if missing; existing rows are updated by ID (local wins).

Auto push when internet returns: set `SYNC_AUTO_ENABLED=true`.

## Architecture

- **Blueprints**: `auth`, `dashboard`, `inventory`, `sales`, `purchases`, `expenses`, `customers`, `vendors`, `reports`, `backup`, `sync`, `settings`, `api`
- **Services**: FIFO stock, ledger, cash book, sales, expenses, sync, backup, dashboard metrics
- **Models**: SQLAlchemy with soft delete, audit log, sync queue
- **Migrations**: `flask db init && flask db migrate && flask db upgrade`

## Hosting on MySQL only (optional)

On a cloud server without offline needs: `OFFLINE_FIRST=false`, `FLASK_ENV=production`, `DATABASE_MODE=production`, then `flask --app run init-db`.

## Security

Change `SECRET_KEY` and default admin password before production. Use HTTPS and restrict network access. Never commit `.env`.

## Next steps (extension points)

- Pull sync from MySQL → SQLite for multi-device
- PDF/Excel export services (`reportlab`, `openpyxl` included)
- Barcode label printing
- i18n message catalogs
- Thermal printer CSS (`@media print` on POS invoice)
