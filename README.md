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
