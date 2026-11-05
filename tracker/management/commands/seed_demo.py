"""Create a demo user with an invented job search, so the app can be shown
without exposing anyone's real applications."""

import datetime
import random

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from tracker.models import Application, Interview, Source, Stage, StageChange

COMPANIES = ["Northwind Logistics", "Blauwerk Energy", "Kiezbank", "Spreeline Mobility", "Fernweh Travel",
             "Lumen Health", "Quantelo", "Hafen Systems", "Brightgrid", "Taktwerk", "Velora Payments",
             "Sternfeld Insurance", "Parcelio", "Moorhaus Media"]
ROLES = ["Backend Engineer", "Software Engineer (Python)", "Integration Developer", "Platform Engineer",
         "Junior Backend Developer", "MuleSoft Developer", "Node.js Developer", "Data Engineer"]
CITIES = ["Berlin", "Munich", "Hamburg", "Remote (DE)", "Frankfurt", "Potsdam"]

# (path through the pipeline, weight)
OUTCOMES = [
    ([Stage.APPLIED], 18),
    ([Stage.APPLIED, Stage.REJECTED], 22),
    ([Stage.APPLIED, Stage.GHOSTED], 16),
    ([Stage.APPLIED, Stage.SCREENING], 6),
    ([Stage.APPLIED, Stage.SCREENING, Stage.REJECTED], 8),
    ([Stage.APPLIED, Stage.SCREENING, Stage.INTERVIEW], 5),
    ([Stage.APPLIED, Stage.INTERVIEW, Stage.REJECTED], 4),
    ([Stage.APPLIED, Stage.SCREENING, Stage.INTERVIEW, Stage.OFFER], 1),
]


class Command(BaseCommand):
    help = "Create user 'demo' (password demo-pass-2026) with ~40 invented applications."

    @transaction.atomic
    def handle(self, *args, **options):
        rng = random.Random(7)
        User = get_user_model()
        user, created = User.objects.get_or_create(username="demo")
        if created:
            user.set_password("demo-pass-2026")
            user.save()
        Application.objects.filter(owner=user).delete()
        user.companies.all().delete()

        now = timezone.now()
        paths, weights = zip(*OUTCOMES, strict=True)
        for _ in range(40):
            company = user.companies.get_or_create(name=rng.choice(COMPANIES))[0]
            applied_at = now - datetime.timedelta(days=rng.randint(2, 70), hours=rng.randint(0, 8))
            app = Application.objects.create(
                owner=user, company=company, role=rng.choice(ROLES), location=rng.choice(CITIES),
                source=rng.choices([s.value for s in Source], weights=[40, 25, 8, 10, 15, 2])[0],
            )
            # Write the history directly so it carries realistic past dates.
            when = applied_at - datetime.timedelta(days=rng.randint(0, 3))
            StageChange.objects.create(application=app, to_stage=Stage.SAVED, changed_at=when)
            stage = Stage.SAVED
            path = rng.choices(paths, weights=weights)[0]
            for step, to_stage in enumerate(path):
                when = applied_at if step == 0 else when + datetime.timedelta(days=rng.randint(3, 16))
                if when > now:
                    break
                StageChange.objects.create(application=app, from_stage=stage, to_stage=to_stage, changed_at=when)
                stage = to_stage
                if to_stage == Stage.INTERVIEW:
                    Interview.objects.create(
                        application=app, kind=Interview.Kind.TECHNICAL,
                        scheduled_at=when + datetime.timedelta(days=4), with_whom="Engineering lead",
                    )
            app.stage = stage
            app.applied_on = timezone.localtime(applied_at).date()
            app.next_follow_up = app.applied_on + datetime.timedelta(days=7) if stage == Stage.APPLIED else None
            app.save()

        for i in range(3):
            Application.objects.create(owner=user, company=user.companies.get_or_create(name=COMPANIES[i])[0],
                                       role=ROLES[i], location="Berlin", source=Source.LINKEDIN)
            StageChange.objects.create(application=Application.objects.latest("id"), to_stage=Stage.SAVED,
                                       changed_at=now)
        self.stdout.write(self.style.SUCCESS("Demo ready. Log in as demo / demo-pass-2026."))
