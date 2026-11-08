"""Data model for a personal job search.

Company ──< Application ──< StageChange
                 │
                 └──< Interview

An Application's `stage` is the current state; every move is recorded as a
StageChange, so the history (and every statistic built on it) survives
later edits. Stage changes go through services.move_to_stage, which
enforces the allowed transitions below.
"""

from django.conf import settings
from django.db import models


class Stage(models.TextChoices):
    SAVED = "saved", "Saved"
    APPLIED = "applied", "Applied"
    SCREENING = "screening", "Screening"
    INTERVIEW = "interview", "Interviewing"
    OFFER = "offer", "Offer"
    ACCEPTED = "accepted", "Accepted"
    DECLINED = "declined", "Declined"
    REJECTED = "rejected", "Rejected"
    WITHDRAWN = "withdrawn", "Withdrawn"
    GHOSTED = "ghosted", "No reply"


# Which stages each stage may move to. Anything not listed is rejected.
TRANSITIONS: dict[str, set[str]] = {
    Stage.SAVED: {Stage.APPLIED, Stage.WITHDRAWN},
    Stage.APPLIED: {Stage.SCREENING, Stage.INTERVIEW, Stage.REJECTED, Stage.WITHDRAWN, Stage.GHOSTED},
    Stage.SCREENING: {Stage.INTERVIEW, Stage.OFFER, Stage.REJECTED, Stage.WITHDRAWN, Stage.GHOSTED},
    Stage.INTERVIEW: {Stage.OFFER, Stage.REJECTED, Stage.WITHDRAWN, Stage.GHOSTED},
    Stage.OFFER: {Stage.ACCEPTED, Stage.DECLINED},
    # Companies do sometimes come back after weeks of silence.
    Stage.GHOSTED: {Stage.SCREENING, Stage.INTERVIEW, Stage.REJECTED},
    Stage.ACCEPTED: set(),
    Stage.DECLINED: set(),
    Stage.REJECTED: set(),
    Stage.WITHDRAWN: set(),
}

ACTIVE_STAGES = [Stage.SAVED, Stage.APPLIED, Stage.SCREENING, Stage.INTERVIEW, Stage.OFFER]
CLOSED_STAGES = [s for s in Stage if s not in ACTIVE_STAGES]
# Any of these after applying means the company replied, good news or bad.
RESPONSE_STAGES = [Stage.SCREENING, Stage.INTERVIEW, Stage.OFFER, Stage.REJECTED]


# How the dashboard groups stages. Each group has a key (used in the URL),
# a label, the stages it covers, and a tone for its colour.
STATUS_GROUPS = [
    ("active", "In progress", [Stage.SAVED, Stage.APPLIED, Stage.SCREENING], "progress"),
    ("interviewing", "Interviewing", [Stage.INTERVIEW], "interview"),
    ("offers", "Offers & hired", [Stage.OFFER, Stage.ACCEPTED], "good"),
    ("rejected", "Rejected", [Stage.REJECTED], "bad"),
    ("noreply", "No reply", [Stage.GHOSTED], "muted"),
    ("withdrawn", "Withdrawn & declined", [Stage.WITHDRAWN, Stage.DECLINED], "muted"),
]
STAGE_TONE = {stage: tone for _, _, stages, tone in STATUS_GROUPS for stage in stages}


class Source(models.TextChoices):
    LINKEDIN = "linkedin", "LinkedIn"
    COMPANY_SITE = "company_site", "Company website"
    REFERRAL = "referral", "Referral"
    RECRUITER = "recruiter", "Recruiter"
    JOB_BOARD = "job_board", "Job board"
    OTHER = "other", "Other"


class Company(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="companies")
    name = models.CharField(max_length=120)
    website = models.URLField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "companies"
        constraints = [models.UniqueConstraint(fields=["owner", "name"], name="unique_company_per_owner")]

    def __str__(self) -> str:
        return self.name


class Application(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="applications")
    company = models.ForeignKey(Company, on_delete=models.RESTRICT, related_name="applications")
    role = models.CharField(max_length=160)
    job_url = models.URLField(blank=True, max_length=500)
    location = models.CharField(max_length=120, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.OTHER)
    stage = models.CharField(max_length=20, choices=Stage.choices, default=Stage.SAVED, editable=False)
    applied_on = models.DateField(null=True, blank=True)
    next_follow_up = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["owner", "stage"]),
            models.Index(fields=["owner", "next_follow_up"]),
        ]

    def __str__(self) -> str:
        return f"{self.role} at {self.company}"

    @property
    def is_active(self) -> bool:
        return self.stage in ACTIVE_STAGES

    def allowed_next_stages(self) -> list[str]:
        return [s for s in Stage if s in TRANSITIONS[self.stage]]


class StageChange(models.Model):
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="changes")
    from_stage = models.CharField(max_length=20, choices=Stage.choices, blank=True)
    to_stage = models.CharField(max_length=20, choices=Stage.choices)
    changed_at = models.DateTimeField()
    note = models.CharField(max_length=300, blank=True)
    # True for history reconstructed by an import, whose real date is unknown.
    # Excluded from timing statistics so it can't skew them.
    backfilled = models.BooleanField(default=False)

    class Meta:
        ordering = ["changed_at", "id"]
        indexes = [models.Index(fields=["application", "to_stage"])]

    def __str__(self) -> str:
        return f"{self.application}: {self.from_stage or '—'} → {self.to_stage}"


class Interview(models.Model):
    class Kind(models.TextChoices):
        PHONE = "phone", "Phone screen"
        TECHNICAL = "technical", "Technical"
        SYSTEM_DESIGN = "system_design", "System design"
        HIRING_MANAGER = "hiring_manager", "Hiring manager"
        ONSITE = "onsite", "On-site / final"
        OTHER = "other", "Other"

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="interviews")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    scheduled_at = models.DateTimeField()
    with_whom = models.CharField(max_length=160, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["scheduled_at"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} — {self.application}"


class UserSettings(models.Model):
    """Per-user preferences, created on first save from the Settings page."""

    class Theme(models.TextChoices):
        SYSTEM = "system", "Match my device"
        LIGHT = "light", "Light"
        DARK = "dark", "Dark"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tracker_settings")
    theme = models.CharField(max_length=10, choices=Theme.choices, default=Theme.SYSTEM)

    class Meta:
        verbose_name_plural = "user settings"

    def __str__(self) -> str:
        return f"Settings for {self.user}"

    @classmethod
    def theme_for(cls, user) -> str:
        if not getattr(user, "is_authenticated", False):
            return cls.Theme.SYSTEM
        return cls.objects.filter(user=user).values_list("theme", flat=True).first() or cls.Theme.SYSTEM
