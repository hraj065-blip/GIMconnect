from django.core.management.base import BaseCommand

from connect.models import User


class Command(BaseCommand):
    help = "Create verified demo accounts."

    def handle(self, *args, **options):
        accounts = [
            ("man@gim.ac.in", "Demo Man", User.Gender.MAN),
            ("woman@gim.ac.in", "Demo Woman", User.Gender.WOMAN),
            ("woman2@gim.ac.in", "Demo Woman Two", User.Gender.WOMAN),
        ]
        for email, name, gender in accounts:
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    "display_name": name,
                    "gender": gender,
                    "email_verified": True,
                    "photo_status": User.PhotoStatus.APPROVED,
                    "onboarding_complete": True,
                },
            )
            if created:
                user.set_password("connect123")
                user.save()
        self.stdout.write(self.style.SUCCESS("Demo accounts ready. Password: connect123"))
