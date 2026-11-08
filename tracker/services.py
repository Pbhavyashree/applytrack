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

    # "Applied" means the history shows the application went out, even when
    # the exact date is unknown (e.g. only the company's reply survived).
    applied = apps.filter(_reached([s for s in Stage if s not in (Stage.SAVED, Stage.WITHDRAWN)]))
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
        if app.first_response and app.applied_on:
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
    "reached": {"reached", "furthest stage", "highest stage", "got to"},
    "responded_on": {"response date", "reply date", "responded on", "outcome date", "last update"},
    "notes": {"notes", "comments"},
}
STAGE_ALIASES = {s.label.lower(): s.value for s in Stage} | {s.value: s.value for s in Stage} | {
    "interview": Stage.INTERVIEW, "no response": Stage.GHOSTED, "ghosted": Stage.GHOSTED,
    "rejection": Stage.REJECTED, "phone screen": Stage.SCREENING, "hired": Stage.ACCEPTED,
}
SOURCE_ALIASES = {s.label.lower(): s.value for s in Source} | {s.value: s.value for s in Source} | {
    "stepstone": Source.JOB_BOARD, "indeed": Source.JOB_BOARD, "xing": Source.JOB_BOARD, "website": Source.COMPANY_SITE,
}
DATE_FORMATS = ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"]
# Order of the "progress" stages, used to fill in history between applying
# and the final outcome.
PROGRESS = [Stage.SCREENING, Stage.INTERVIEW, Stage.OFFER]


@dataclass
class ImportResult:
    created: int = 0
    skipped: int = 0
    replaced: int = 0
    errors: list[str] = field(default_factory=list)


def _parse_date(value: str) -> datetime.date | None:
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {value!r}")


def _stage(text: str, column: str) -> str | None:
    if not text:
        return None
    stage = STAGE_ALIASES.get(text.lower())
    if stage is None:
        raise ValueError(f"unknown {column} {text!r}")
    return stage


@transaction.atomic
def import_csv(owner, text: str, replace: bool = False) -> ImportResult:
    """Import applications from a spreadsheet export.

    Column names are matched loosely ("Status" or "Stage", "Position" or
    "Role"). Rows already imported are skipped, so re-running is safe.
    Each row is all-or-nothing; a bad row is reported and the rest continue.

    With replace=True the user's existing applications are deleted first, in
    the same transaction: if the file can't be read at all, nothing is lost.
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

    if replace:
        result.replaced = Application.objects.filter(owner=owner).count()
        Application.objects.filter(owner=owner).delete()
        Company.objects.filter(owner=owner, applications__isnull=True).delete()

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


def _at_nine(day: datetime.date) -> datetime.datetime:
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time(9)))


def _import_row(owner, row: dict) -> bool:
    if not row.get("company") or not row.get("role"):
        raise ValueError("company and role are required")
    applied_on = _parse_date(row["applied_on"]) if row.get("applied_on") else None
    responded_on = _parse_date(row["responded_on"]) if row.get("responded_on") else None
    stage = _stage(row.get("stage", ""), "status") or (Stage.APPLIED if applied_on else Stage.SAVED)
    reached = _stage(row.get("reached", ""), "reached stage")
    if reached and reached not in PROGRESS:
        raise ValueError(f"reached stage must be screening, interview or offer, not {row['reached']!r}")
    if applied_on and responded_on and responded_on < applied_on:
        raise ValueError("response date is before the applied date")
    source = SOURCE_ALIASES.get(row.get("source", "").lower(), Source.OTHER)

    if Application.objects.filter(
        owner=owner, company__name__iexact=row["company"], role__iexact=row["role"], applied_on=applied_on
    ).exists():
        return False

    company = Company.objects.filter(owner=owner, name__iexact=row["company"]).first()
    company = company or Company.objects.create(owner=owner, name=row["company"])
    active = stage in ACTIVE_STAGES
    app = Application.objects.create(
        owner=owner, company=company, role=row["role"], job_url=row.get("job_url", ""),
        location=row.get("location", ""), source=source, stage=stage, applied_on=applied_on,
        next_follow_up=applied_on + FOLLOW_UP_AFTER if active and stage == Stage.APPLIED and applied_on else None,
        notes=row.get("notes", ""),
    )

    # Rebuild the history: saved -> applied -> progress stages reached -> outcome.
    # A step gets a real date only when the file gives one; otherwise it is
    # flagged as backfilled and kept out of timing statistics.
    # Without an applied date, anchor the history on the reply date so the
    # application doesn't look like it happened today.
    anchor = applied_on or responded_on
    start = _at_nine(anchor) if anchor else timezone.now()
    steps = [(Stage.SAVED, start, False)]
    if applied_on:
        steps.append((Stage.APPLIED, start, False))
    # Only stages the file actually states, never the ones in between: an
    # accepted offer doesn't prove there was a separate screening call.
    progress = {s for s in (reached, stage) if s in PROGRESS}
    if stage in (Stage.ACCEPTED, Stage.DECLINED):
        progress.add(Stage.OFFER)
    outcome_at = _at_nine(responded_on) if responded_on else start
    for step in sorted(progress, key=PROGRESS.index):
        steps.append((step, outcome_at, not (step == stage and responded_on)))
    if stage not in [s for s, _, _ in steps]:
        steps.append((stage, outcome_at, responded_on is None))

    previous = ""
    for to_stage, when, backfilled in steps:
        StageChange.objects.create(application=app, from_stage=previous, to_stage=to_stage,
                                   changed_at=when, backfilled=backfilled, note="Imported")
        previous = to_stage
    return True
