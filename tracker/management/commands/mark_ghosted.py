from django.core.management.base import BaseCommand

from tracker import services


class Command(BaseCommand):
    help = "Move applications with no reply for N days to 'No reply'. Safe to run daily from cron."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)

    def handle(self, *args, days, **options):
        moved = services.mark_ghosted(days=days)
        for app in moved:
            self.stdout.write(f"No reply: {app}")
        self.stdout.write(self.style.SUCCESS(f"{len(moved)} application(s) marked."))
