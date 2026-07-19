from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()


class LastActiveMiddleware:
    """Refresh last_active_at for authenticated app usage, throttled per hour."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)

        if (
            user is not None
            and user.is_authenticated
            and request.path_info.startswith("/app/")
        ):
            now = timezone.now()
            last_active_at = getattr(user, "last_active_at", None)

            if last_active_at is None or now - last_active_at >= timedelta(hours=1):
                User.objects.filter(pk=user.pk).update(last_active_at=now)
                user.last_active_at = now

        return self.get_response(request)
