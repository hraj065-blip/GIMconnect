from django.core.management.base import BaseCommand

from connect.services import process_timers


class Command(BaseCommand):
    help = "Expire lobby requests, connections, and reveal windows."

    def handle(self, *args, **options):
        expired_requests = process_timers()
        self.stdout.write(self.style.SUCCESS(f"Expired {expired_requests} lobby requests."))
