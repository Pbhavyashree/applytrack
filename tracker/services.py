"""Business logic shared by the API, web pages, admin and management commands."""

import csv
import datetime
import io
import statistics
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Count, Exists, Max, Min, OuterRef, Q
from django.utils import timezone

from .models import (
    ACTIVE_STAGES,
    RESPONSE_STAGES,
    TRANSITIONS,
    Application,
    Company,
    Source,
    Stage,
    StageChange,
)

FOLLOW_UP_AFTER = datetime.timedelta(days=7)


class TransitionError(ValueError):
    pass


# --- Creating and moving applications --------------------------------------


@transaction.atomic
def create_application(owner, *, company_name: str, role: str, stage: str = Stage.SAVED, **fields) -> Application:
    if stage not in (Stage.SAVED, Stage.APPLIED):
        raise TransitionError("New applications start as saved or applied.")
    applied_on = fields.pop("applied_on", None)
    fields.pop("next_follow_up", None)  # derived from the applied date
    company = Company.objects.filter(owner=owner, name__iexact=company_name.strip()).first()
    if company is None:
        company = Company.objects.create(owner=owner, name=company_name.strip())
    app = Application.objects.create(owner=owner, company=company, role=role.strip(), **fields)
    StageChange.objects.create(application=app, to_stage=Stage.SAVED, changed_at=timezone.now())
    if stage == Stage.APPLIED:
        app = move_to_stage(app, Stage.APPLIED, on=applied_on)
    return app


@transaction.atomic
def move_to_stage(app: Application, to_stage: str, note: str = "", on: datetime.date | None = None) -> Application:
    """Move an application along the pipeline, recording the change.

    Also keeps the dates consistent: applying sets `applied_on` and a
    follow-up a week later; any reply clears the follow-up reminder.
    """
    app = Application.objects.select_for_update().get(pk=app.pk)
    if to_stage not in TRANSITIONS.get(app.stage, set()):
        raise TransitionError(f"Can't move from {app.get_stage_display()} to {Stage(to_stage).label}.")

    now = timezone.now()
    if to_stage == Stage.APPLIED:
        app.applied_on = on or timezone.localdate()
        app.next_follow_up = app.applied_on + FOLLOW_UP_AFTER
    elif to_stage in RESPONSE_STAGES or to_stage not in ACTIVE_STAGES:
        app.next_follow_up = None

    StageChange.objects.create(
        application=app, from_stage=app.stage, to_stage=to_stage, changed_at=now, note=note[:300]
    )
    app.stage = to_stage
    app.save()
    return app


def follow_ups_due(owner, today: datetime.date | None = None):
    today = today or timezone.localdate()
    return (
        Application.objects.filter(owner=owner, stage__in=ACTIVE_STAGES, next_follow_up__lte=today)
        .select_related("company")
        .order_by("next_follow_up")
    )


def mark_ghosted(owner=None, days: int = 30) -> list[Application]:
    """Move applications with no reply for `days` days to No reply."""
    cutoff = timezone.now() - datetime.timedelta(days=days)
    stale = Application.objects.filter(stage=Stage.APPLIED).annotate(last_change=Max("changes__changed_at"))
    if owner is not None:
        stale = stale.filter(owner=owner)
    moved = []
    for app in stale.filter(last_change__lt=cutoff):
        moved.append(move_to_stage(app, Stage.GHOSTED, note=f"No reply in {days} days"))
    return moved


# --- Statistics ------------------------------------------------------------


@dataclass
class SourceStats:
    source: str
    label: str
    applied: int
    responded: int

    @property
    def response_rate(self) -> float | None:
        return round(self.responded / self.applied, 3) if self.applied else None


@dataclass
class Stats:
    applied: int = 0
    responded: int = 0
    interviewing: int = 0
    offers: int = 0
    active: int = 0
    median_days_to_response: float | None = None
    by_stage: dict[str, int] = field(default_factory=dict)
    by_source: list[SourceStats] = field(default_factory=list)

    @property
    def response_rate(self) -> float | None:
        return round(self.responded / self.applied, 3) if self.applied else None

    @property
    def interview_rate(self) -> float | None:
        return round(self.interviewing / self.applied, 3) if self.applied else None


def _reached(stages):
    return Exists(StageChange.objects.filter(application=OuterRef("pk"), to_stage__in=stages))


def pipeline_stats(owner, since: datetime.date | None = None) -> Stats:
    """Funnel numbers from the stage history, not just current stages,
    so an application that was interviewed and then rejected still
    counts as having reached interview."""
    apps = Application.objects.filter(owner=owner)
    stats = Stats(
        by_stage=dict(apps.values_list("stage").annotate(n=Count("id")).order_by()),
        active=apps.filter(stage__in=ACTIVE_STAGES).count(),
    )

    applied = apps.filter(applied_on__isnull=False)
    if since:
        applied = applied.filter(applied_on__gte=since)
    applied = applied.annotate(
        responded=_reached(RESPONSE_STAGES),
        interviewed=_reached([Stage.INTERVIEW]),
        offered=_reached([Stage.OFFER]),
        first_response=Min(
            "changes__changed_at",
            filter=Q(changes__to_stage__in=RESPONSE_STAGES, changes__backfilled=False),
        ),
    )

    gaps = []
    per_source: dict[str, list[int]] = {}
    for app in applied:
        stats.applied += 1
        stats.responded += app.responded
        stats.interviewing += app.interviewed
        stats.offers += app.offered
        counts = per_source.setdefault(app.source, [0, 0])
        counts[0] += 1
        counts[1] += app.responded
        if app.first_response:
            gaps.append((timezone.localtime(app.first_response).date() - app.applied_on).days)

    stats.median_days_to_response = statistics.median(gaps) if gaps else None
    stats.by_source = sorted(
        (SourceStats(s, Source(s).label, a, r) for s, (a, r) in per_source.items()),
        key=lambda s: -s.applied,
    )
    return stats


# --- CSV import ------------------------------------------------------------

COLUMN_ALIASES = {
    "company": {"company", "company name", "employer"},
    "role": {"role", "position", "job title", "title"},
    "job_url": {"url", "link", "job url", "job link"},
    "location": {"location", "city"},
    "source": {"source", "found via", "channel"},
    "applied_on": {"applied", "applied on", "date applied", "date"},
    "stage": {"stage", "status"},
    "notes": {"notes", "comments"},
}
STAGE_ALIASES = {s.label.lower(): s.value for s in Stage} | {s.value: s.value for s in Stage} | {
    "interview": Stage.INTERVIEW, "no response": Stage.GHOSTED, "ghosted": Stage.GHOSTED,
    "rejection": Stage.REJECTED, "phone screen": Stage.SCREENING,
}
SOURCE_ALIASES = {s.label.lower(): s.value for s in Source} | {s.value: s.value for s in Source} | {
    "stepstone": Source.JOB_BOARD, "indeed": Source.JOB_BOARD, "xing": Source.JOB_BOARD, "website": Source.COMPANY_SITE,
}
DATE_FORMATS = ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"]


@dataclass
class ImportResult:
    created: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def _parse_date(value: str) -> datetime.date | None:
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {value!r}")


def import_csv(owner, text: str) -> ImportResult:
    """Import applications from a spreadsheet export.

    Column names are matched loosely ("Status" or "Stage", "Position" or
    "Role"). Rows already imported are skipped, so re-running is safe.
    Each row is all-or-nothing; a bad row is reported and the rest continue.
    """
    result = ImportResult()
    text = text.lstrip("\ufeff")
    # German-locale Excel saves CSV with semicolons; detect it from the header.
    first_line = text.split("\n", 1)[0]
    delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    mapping = {}
    for header in reader.fieldnames or []:
        for field_name, aliases in COLUMN_ALIASES.items():
            if header.strip().lower() in aliases:
                mapping[header] = field_name
    if not {"company", "role"} <= set(mapping.values()):
        result.errors.append("CSV needs at least a company and a role column.")
        return result

    for line_no, raw in enumerate(reader, start=2):
        row = {mapping[k]: (v or "").strip() for k, v in raw.items() if k in mapping}
        try:
            with transaction.atomic():
                if _import_row(owner, row):
                    result.created += 1
                else:
                    result.skipped += 1
        except (ValueError, KeyError) as exc:
            result.errors.append(f"Row {line_no}: {exc}")
    return result


def _import_row(owner, row: dict) -> bool:
    if not row.get("company") or not row.get("role"):
        raise ValueError("company and role are required")
    applied_on = _parse_date(row["applied_on"]) if row.get("applied_on") else None
    stage_text = row.get("stage", "").lower()
    stage = STAGE_ALIASES.get(stage_text) if stage_text else (Stage.APPLIED if applied_on else Stage.SAVED)
    if stage is None:
        raise ValueError(f"unknown stage {row['stage']!r}")
    source = SOURCE_ALIASES.get(row.get("source", "").lower(), Source.OTHER)

    if Application.objects.filter(
        owner=owner, company__name__iexact=row["company"], role__iexact=row["role"], applied_on=applied_on
    ).exists():
        return False

    company = Company.objects.filter(owner=owner, name__iexact=row["company"]).first()
    company = company or Company.objects.create(owner=owner, name=row["company"])
    app = Application.objects.create(
        owner=owner, company=company, role=row["role"], job_url=row.get("job_url", ""),
        location=row.get("location", ""), source=source, stage=stage, applied_on=applied_on,
        notes=row.get("notes", ""),
    )
    # Reconstruct a minimal history. The date of a later stage is unknown,
    # so that step is flagged as backfilled and kept out of timing stats.
    if applied_on:
        when = timezone.make_aware(datetime.datetime.combine(applied_on, datetime.time(9)))
    else:
        when = timezone.now()
    history = [("", Stage.SAVED, False)]
    if applied_on:
        history.append((Stage.SAVED, Stage.APPLIED, False))
    if history[-1][1] != stage:
        history.append((history[-1][1], stage, True))
    for from_stage, to_stage, backfilled in history:
        StageChange.objects.create(application=app, from_stage=from_stage, to_stage=to_stage,
                                   changed_at=when, backfilled=backfilled, note="Imported")
    return True
