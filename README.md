# GIM Connect

A Django implementation of the anonymous-first GIM campus dating blueprint.

## Included

- GIM-domain signup, email OTP, verification-photo review, onboarding, and Django admin
- Gender-specific connection caps, one outgoing request per rolling 24 hours, lobby queue, expiry/refund, blocking, and seven-day rematch cooldown
- Anonymous text chat with polling, connection expiry/end controls, reports, and 48-hour safety suspensions
- Corrected two-path identity reveal flow with the woman's reconfirmation step and protected three-minute photo window
- Timer-processing and demo-seeding management commands

AI bootstrap is intentionally disabled. Any future AI participation should be clearly disclosed to users.

## Local setup

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_demo
.venv/bin/python manage.py runserver
```

Demo accounts use `connect123`:

- `man@gim.ac.in`
- `woman@gim.ac.in`
- `woman2@gim.ac.in`

Run `.venv/bin/python manage.py process_connect_timers` every minute in production. Configure the allowed institutional domains with `GIM_ALLOWED_EMAIL_DOMAINS`.

## Vercel deployment notes

The app is configured for Vercel serverless deployment:

- `/static/*` is served directly from the committed `static/` folder.
- WhiteNoise is not required at runtime, so the app no longer crashes if `/var/task/staticfiles/` is absent.
- `build.sh` still runs `collectstatic` so Django admin assets are prepared during build.

Set these Vercel environment variables before production use:

```text
DJANGO_SECRET_KEY=<long-random-secret>
DJANGO_DEBUG=False
DATABASE_URL=<your-postgres-url>
EMAIL_HOST_USER=<otp-sender-email>
EMAIL_HOST_PASSWORD=<smtp-or-app-password>
DEFAULT_FROM_EMAIL=GIM Connect <your-sender-email>
GIM_ALLOWED_EMAIL_DOMAINS=gim.ac.in
```

If you use a custom domain, also set:

```text
CUSTOM_DOMAIN=yourdomain.com
```

