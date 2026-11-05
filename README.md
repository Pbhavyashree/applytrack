# Applytrack

A job application tracker that replaces the spreadsheet. Every application moves through a defined pipeline (saved → applied → screening → interviewing → offer), each move is recorded with a date and note, and the dashboard turns that history into numbers worth knowing: reply rate, interview rate, median time to a reply, and which sources actually get answers.

Built with Django 5.2, Django REST Framework and PostgreSQL.

## Features

- **Pipeline board** with active applications by stage, plus a separate list of closed ones.
- **Stage rules.** Applications can only move along allowed paths, so you can't jump from applied to accepted or reopen a rejection. Companies that go quiet can come back from "No reply".
- **Full history.** Every move is a `StageChange` row, so statistics count what an application *reached*. An application that was interviewed and then rejected still counts as having reached interview.
- **Follow-up reminders.** Applying sets a follow-up a week later; any reply clears it.
- **Interviews** per application. Booking one moves the application to Interviewing.
- **Stats:** reply rate, interview rate, offers, median days to first reply, reply rate by source.
- **CSV import** from an existing spreadsheet, with loose column matching, German or ISO dates, per-row error reporting and safe re-runs.
- **Auto "No reply"**: `manage.py mark_ghosted --days 30` closes applications that never got an answer.
- **Multi-user.** Every query is scoped to the signed-in user, in the API, the pages and the admin.

## Design notes

| Area | Where | What to look at |
| --- | --- | --- |
| State machine | `tracker/models.py` (`TRANSITIONS`), `services.move_to_stage` | Transitions validated in one place, row locked with `select_for_update`, stage is `editable=False` so forms and the admin can't bypass it |
| Statistics | `services.pipeline_stats` | `Exists` subqueries over history and a filtered `Min` for first reply, all in one query |
| Import | `services.import_csv` | Each row in its own transaction; history of unknown date flagged `backfilled` and excluded from timing stats so it can't skew the median |
| Admin | `tracker/admin.py` | Per-owner querysets, read-only history inline, bulk actions that go through the state machine, CSV export |
| API | `tracker/views.py`, `serializers.py` | ViewSets with custom actions (`move`, `follow-ups`, `import`), create-by-company-name, 409 for invalid transitions |
| Access | everywhere | Other users' rows return 404, not 403, so their existence isn't revealed |

## API

Authenticate with the session cookie or `Authorization: Token <key>` (from `POST /api/token/`).

| Method | Path | Purpose |
| --- | --- | --- |
| GET, POST | `/api/applications/` | List (filters: `stage`, `source`, `active`, `q`) or create |
| GET, PATCH, DELETE | `/api/applications/{id}/` | Detail includes history and interviews |
| POST | `/api/applications/{id}/move/` | `{"stage": "interview", "note": "..."}`; 409 if not allowed |
| GET | `/api/applications/follow-ups/` | Follow-ups due today or earlier |
| POST | `/api/applications/import/` | Multipart CSV upload |
| GET | `/api/stats/?since=YYYY-MM-DD` | Funnel and per-source numbers |
| CRUD | `/api/companies/`, `/api/interviews/` | |

Creating an application takes a company name; the company is reused if you already have one with that name (case-insensitive):

```http
POST /api/applications/
{"company_name": "Kiezbank", "role": "Backend Engineer", "stage": "applied", "source": "referral"}
```

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export DJANGO_DEBUG=1
python manage.py migrate
python manage.py seed_demo          # user demo / demo-pass-2026 with ~40 invented applications
python manage.py runserver
```

To use it for real, create your own account with `python manage.py createsuperuser` and import your spreadsheet from the Import page or with:

```bash
python manage.py import_applications tracker.csv --user yourname
```

## Tests

```bash
pytest
```

49 tests cover valid and invalid stage paths, follow-up dates, the ghosting job, every statistic, CSV import edge cases (aliases, bad dates, BOM, duplicates, backfilled history), and that users can't read or change each other's data through the API, the pages or the admin. CI runs lint, a migration drift check, `check --deploy` and the tests against PostgreSQL 17.

## Deploy

```bash
cp .env.example .env    # secret key, hostname, DB password
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

The container listens on `127.0.0.1:8004`. Caddy site block:

```
applytrack.example.duckdns.org {
    reverse_proxy 127.0.0.1:8004
}
```

Optional daily cron on the host:

```
0 8 * * * cd /opt/applytrack && docker compose exec -T web python manage.py mark_ghosted --days 30
```
