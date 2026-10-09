# Using a free, secure cloud database (Neon + PostGIS)

The collector reads its database connection from `DATABASE_URL` in `.env`
(see `.env.example`). To move off the local Postgres.app and onto a managed,
free, TLS-secured Postgres, use **[Neon](https://neon.tech)** — serverless
Postgres with a generous free tier, SSL required by default, and one-click
PostGIS.

> Alternative: **[Supabase](https://supabase.com)** also gives free Postgres
> with PostGIS pre-available. The steps are the same; just use its connection
> string. Neon is recommended here for being the leanest pure-Postgres option.

## 1. Create the database (Neon)

1. Sign up at https://neon.tech (free, no card).
2. Create a project — pick the region closest to you.
3. In the project, create a database named `tagtrace` (or use the default).
4. Open **Connection Details** and copy the **connection string**. It looks
   like:
   ```
   postgresql://USER:PASSWORD@ep-xxx-123.us-east-2.aws.neon.tech/tagtrace?sslmode=require
   ```
   Neon enforces TLS, so `sslmode=require` is already in the string — keep it.

## 2. Point the collector at it

In `GoogleFindMyTools/.env`, uncomment `DATABASE_URL` and paste the string,
changing the scheme to `postgresql+psycopg2://`:

```
DATABASE_URL=postgresql+psycopg2://USER:PASSWORD@ep-xxx-123.us-east-2.aws.neon.tech/tagtrace?sslmode=require
```

When `DATABASE_URL` is set it overrides the local `PG*` settings. Nothing else
changes. `.env` is gitignored, so your credentials are never committed.

## 3. First run

```
source .venv/bin/activate
python3 -m pip install -r requirements-collector.txt
python3 collector.py
```

On startup the collector will, against the cloud database:
- enable PostGIS (`CREATE EXTENSION IF NOT EXISTS postgis`),
- create the `locations`, `users`, `sessions`, `shares` tables,
- add the spatial `locations.geom` column + GIST index.

If the account can't enable PostGIS it logs a notice and keeps running without
the spatial column — the app still works.

## 4. Re-create your login on the new database

Users live in the database, so the cloud DB starts with none. Create yours:

```
python3 collector.py --add-user you@example.com --password 'choose-a-strong-password'
```

## 5. (Optional) Move your existing points over

The cloud DB starts empty. To copy your local history across:

```
# dump just the data from the local DB
pg_dump "postgresql://$USER@127.0.0.1:5432/tagtrace" \
  --data-only --table=locations --no-owner > locations.sql

# load it into the cloud DB (run the collector once first so the table exists)
psql "postgresql://USER:PASSWORD@ep-xxx.aws.neon.tech/tagtrace?sslmode=require" \
  -f locations.sql
```

`geom` is a generated column, so it is recomputed automatically for every
imported row — no need to move it.

---

## Spatial queries you can now run

Because coordinates are stored as `GEOMETRY(Point, 4326)`, PostGIS can answer
spatial questions directly. A few examples:

```sql
-- Points within 500 m of a location (lng, lat), using metres via geography:
SELECT t, lat, lng
FROM locations
WHERE ST_DWithin(
    geom::geography,
    ST_SetSRID(ST_MakePoint(72.8777, 19.0760), 4326)::geography,
    500
);

-- Total distance travelled in a day, in metres:
SELECT ST_Length(ST_MakeLine(geom ORDER BY t)::geography)
FROM locations
WHERE ts::date = CURRENT_DATE;

-- Did the device enter a zone (a polygon you define)?
SELECT EXISTS (
    SELECT 1 FROM locations
    WHERE ST_Contains(
        ST_GeomFromText('POLYGON((...))', 4326),
        geom
    )
);
```

The design keeps the plain `lat`/`lng` columns the Android app and web UI
already read, and adds `geom` alongside them — so you get spatial power without
changing the API. If you later want to run these queries through the ORM rather
than raw SQL, add `geoalchemy2` to the requirements and map the column with
`Geometry("POINT", srid=4326)`.
