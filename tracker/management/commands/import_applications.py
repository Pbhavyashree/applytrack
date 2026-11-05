from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tracker import services


class Command(BaseCommand):
    help = "Import applications from a CSV export of a spreadsheet tracker."

    def add_arguments(self, parser):
        parser.add_argument("csv_path", type=Path)
        parser.add_argument("--user", required=True, help="Username to import for")

    def handle(self, *args, csv_path, user, **options):
        try:
            owner = get_user_model().objects.get(username=user)
        except get_user_model().DoesNotExist as exc:
            raise CommandError(f"No user {user!r}") from exc
        result = services.import_csv(owner, csv_path.read_text(encoding="utf-8-sig"))
        for err in result.errors:
            self.stderr.write(err)
        self.stdout.write(self.style.SUCCESS(f"{result.created} imported, {result.skipped} already there."))
