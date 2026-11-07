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

1. Upload the whole `online_portal` folder (e.g. `public_html/mbf-portal` or a subdomain app root).
2. Create Python app / Passenger pointing at this folder (`passenger_wsgi.py`).
3. Install deps: `pip install -r requirements.txt`
4. Confirm Remote MySQL allows the server (and your PC for seeding).
5. Seed Super Admin:

```bash
cd online_portal
python seed_super_admin.py
```

Default Super Admin (from `.env`):

- Username: `U. Technologies`
- Password: from `SUPER_ADMIN_PASSWORD`

6. Open the site → login page.

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
