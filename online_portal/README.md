# Agri Books — Online Portal

Domain: **https://agribooks.udottechnologies.com**

Separate web app (not the full desktop ERP). Same Agri Books UI (sidebar + topbar + company branding). Host this folder on the agribooks domain.


## Roles

| Role | Where | Access |
|------|--------|--------|
| Super Admin | Online | All companies/users, logos, inactive, regenerate keys |
| Company Admin | Online | Own company dashboard, customers, vendors, ledgers — **view only** |
| Shop Clerk | Offline app | Add + view (wired in desktop app next) |

## MySQL

Database: `mygymlahore_mian_brothers_fertilizer`  
Credentials live in `online_portal/.env` (never commit).

## Setup on server (new folder)

1. Upload the whole `online_portal` folder to the agribooks domain app root.
2. **Setup Python App** → entry `passenger_wsgi.py`.
3. Install deps: `pip install -r requirements.txt`
4. **Migrate schema once** (do not put `create_all` on worker start):

```bash
cd online_portal
python scripts/migrate_schema.py
python seed_super_admin.py
```

5. In Setup Python App → Environment variables, set:

```
LSAPI_CHILDREN=3
LSAPI_MAX_IDLE=120
LSAPI_MAX_IDLE_CHILDREN=3
LSAPI_PGRP_MAX_IDLE=300
```

Then **Stop** and **Start** the Python app.

6. `.env`: MySQL URI, `USE_LOCAL_SQLITE=false`, Super Admin password.

Default Super Admin username: `U. Technologies`

**Important:** `db.create_all()` must never run inside `create_app()` / Passenger workers — that pattern suspended the account before. Schema only via `scripts/migrate_schema.py`.

## Local run

```bash
cd online_portal
pip install -r requirements.txt
python seed_super_admin.py
python run.py
```

Visit http://127.0.0.1:5055/

## Desktop sync

Point the offline app `.env` `MYSQL_DATABASE_URI` at this same database so Sync / daily auto-sync fills customer & vendor tables the portal reads.

## Existing shop (Saeed Hanif)

After MySQL access works, create his **Company** in Super Admin, then a **Company Admin** user for online view, and keep/link his offline registration key under that company.
