from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from .models import Block, Connection, ConnectionRequest, Report, RevealRequest, User
from .notifications import notify_new_connection

DAY = timedelta(hours=24)
CONNECTION_DURATION = timedelta(days=7)
REMATCH_COOLDOWN = timedelta(days=7)
REVEAL_WINDOW = timedelta(minutes=3)


def active_count(user):
    return user.active_connections().count()


def _blocked_pair(a, b):
    return Block.objects.filter(
        Q(blocker=a, blocked=b) | Q(blocker=b, blocked=a)
    ).exists()


def _recently_connected(a, b, now):
    """True if a and b had a connection that ended within the 7-day rematch cooldown."""
    return Connection.objects.filter(
        Q(man=a, woman=b) | Q(man=b, woman=a),
        status__in=[Connection.Status.ENDED, Connection.Status.EXPIRED],
        ended_at__gt=now - REMATCH_COOLDOWN,
    ).exists()


def _eligible_candidates(requester, now):
    opposite = User.Gender.WOMAN if requester.gender == User.Gender.MAN else User.Gender.MAN
 
    base_filters = dict(
        gender=opposite,
        email_verified=True,
        onboarding_complete=True,
        is_blocked=False,
        is_active=True,
        is_available=True,          # paused users excluded from pool
    )
    # Photo is optional for both genders — no photo_status gate in matching.

    candidates = User.objects.filter(**base_filters).exclude(pk=requester.pk)
    if getattr(settings, "ENABLE_AUTOPAUSE_FEATURE", True):
        candidates = candidates.filter(last_active_at__gte=now - timedelta(days=45))
    candidates = candidates.annotate(
        active_total=Count(
            "connections_as_woman" if opposite == User.Gender.WOMAN else "connections_as_man",
            filter=Q(
                **{
                    (
                        "connections_as_woman__status"
                        if opposite == User.Gender.WOMAN
                        else "connections_as_man__status"
                    ): Connection.Status.ACTIVE
                }
            ),
        )
    ).order_by("active_total", "date_joined")
    return [
        user
        for user in candidates
        if user.active_total < user.connection_cap
        and not user.is_suspended
        and not _blocked_pair(requester, user)
        and not _recently_connected(requester, user, now)
    ]


def _ensure_can_request(user, now):
    if not user.is_eligible:
        raise PermissionDenied(
            "Complete verification and onboarding before requesting a connection."
        )
    if active_count(user) >= user.connection_cap:
        raise ValidationError("You are already at your active connection limit.")
    if ConnectionRequest.objects.filter(
        requester=user, status=ConnectionRequest.Status.QUEUED
    ).exists():
        raise ValidationError("You already have a request waiting in the lobby.")
    if user.request_available_at > now:
        raise ValidationError("Your next outgoing request is not available yet.")


@transaction.atomic
def request_connection(user, now=None):
    now = now or timezone.now()
    user = User.objects.select_for_update().get(pk=user.pk)
    _ensure_can_request(user, now)
    request = ConnectionRequest.objects.create(requester=user, expires_at=now + DAY)
    candidates = _eligible_candidates(user, now)
    if candidates:
        connection = establish_connection(user, candidates[0], now=now)
        request.status = ConnectionRequest.Status.MATCHED
        request.matched_connection = connection
        request.ended_at = now
        request.save(update_fields=["status", "matched_connection", "ended_at"])
        return request, connection
    return request, None


@transaction.atomic
def establish_connection(a, b, now=None):
    now = now or timezone.now()
    if a.gender == b.gender:
        raise ValidationError("Connections require compatible genders in this version.")
    man = a if a.gender == User.Gender.MAN else b
    woman = b if b.gender == User.Gender.WOMAN else a
    if active_count(man) >= man.connection_cap or active_count(woman) >= woman.connection_cap:
        raise ValidationError("One of these users is at their connection limit.")
    connection = Connection.objects.create(
        man=man,
        woman=woman,
        established_at=now,
        expires_at=now + CONNECTION_DURATION,
    )
    # Every new real connection (re)starts the man's rolling 24-hour request
    # window. Only advance the timer — never push it backwards.
    if man.request_available_at < now + DAY:
        man.request_available_at = now + DAY
        man.save(update_fields=["request_available_at"])
    # If the woman was the requester (a is the woman), start her outgoing-request
    # timer too. If she received this connection (b is the woman), her timer is
    # unaffected — she didn't spend a request slot.
    if a.gender == User.Gender.WOMAN:
        a.request_available_at = now + DAY
        a.save(update_fields=["request_available_at"])
    transaction.on_commit(lambda: notify_new_connection(connection.pk))
    return connection


@transaction.atomic
def end_connection(connection, ended_by=None, now=None, status=Connection.Status.ENDED):
    now = now or timezone.now()
    connection = (
        Connection.objects.select_for_update()
        .select_related("man", "woman")
        .get(pk=connection.pk)
    )
    if connection.status != Connection.Status.ACTIVE:
        return connection
    connection.status = status
    connection.ended_at = now
    connection.ended_by = ended_by
    connection.save(update_fields=["status", "ended_at", "ended_by"])

    # ── BUG FIX (CRITICAL) ────────────────────────────────────────────────────
    # The original code had this block here:
    #
    #   if man_active_before >= connection.man.connection_cap \
    #           and connection.man.request_available_at <= now:
    #       connection.man.request_available_at = now + DAY
    #       connection.man.save(update_fields=["request_available_at"])
    #
    # When the man was at his cap AND his 24-hour timer had already expired
    # (request_available_at <= now means he could request RIGHT NOW), ending a
    # connection would push his timer 24 hours into the future, delaying him by
    # a full day when he should have been able to request immediately.
    #
    # The man's request timer is owned entirely by establish_connection. Nothing
    # in end_connection should modify it. Removed.
    # ──────────────────────────────────────────────────────────────────────────

    match_waiting_requests(now=now)
    return connection


@transaction.atomic
def cancel_request(request, now=None):
    now = now or timezone.now()
    request = ConnectionRequest.objects.select_for_update().get(pk=request.pk)
    if request.status != ConnectionRequest.Status.QUEUED:
        raise ValidationError("Only a waiting request can be cancelled.")
    request.status = ConnectionRequest.Status.CANCELLED
    request.ended_at = now
    request.save(update_fields=["status", "ended_at"])


@transaction.atomic
def match_waiting_requests(now=None):
    now = now or timezone.now()
    matched = []
    for request in (
        ConnectionRequest.objects.select_for_update()
        .filter(status=ConnectionRequest.Status.QUEUED, expires_at__gt=now)
        .select_related("requester")
    ):
        if not request.requester.is_eligible:
            continue
        candidates = _eligible_candidates(request.requester, now)
        if not candidates or active_count(request.requester) >= request.requester.connection_cap:
            continue
        connection = establish_connection(request.requester, candidates[0], now=now)
        request.status = ConnectionRequest.Status.MATCHED
        request.matched_connection = connection
        request.ended_at = now
        request.save(update_fields=["status", "matched_connection", "ended_at"])
        matched.append(connection)
    return matched


@transaction.atomic
def process_timers(now=None):
    now = now or timezone.now()
    expired_requests = ConnectionRequest.objects.filter(
        status=ConnectionRequest.Status.QUEUED, expires_at__lte=now
    )
    expired_request_count = expired_requests.update(
        status=ConnectionRequest.Status.EXPIRED, ended_at=now
    )

    # ── FIX: connection lifetime auto-expiry ─────────────────────────────────
    # Connections must actually close on the backend once expires_at (7 days
    # after establishment) passes — the frontend countdown alone doesn't end
    # anything. ended_at is set to expires_at via F(), not `now`: this job only
    # runs opportunistically off page loads (_maybe_process_timers, throttled
    # to once/60s), so `now` can lag the real expiry moment by anywhere from
    # seconds to hours depending on when someone next opens the app. expires_at
    # is the timestamp the connection was actually scheduled to end, so
    # ended_at — and anything that reads it, like the 7-day rematch cooldown in
    # _recently_connected — stays accurate regardless of when this runs.
    Connection.objects.filter(
        status=Connection.Status.ACTIVE, expires_at__lte=now
    ).update(status=Connection.Status.EXPIRED, ended_at=F("expires_at"))

    # ── FIX: actively pause accounts inactive for > 45 days ──────────────────
    # is_eligible and _eligible_candidates already read last_active_at to keep
    # stale users out of the matching pool, but nothing ever flipped
    # is_available to False on the account itself, so a dormant user's own
    # dashboard would still show them as "available" indefinitely. This makes
    # the pause explicit and persisted.
    if getattr(settings, "ENABLE_AUTOPAUSE_FEATURE", True):
        inactive_before = now - timedelta(days=45)
        User.objects.filter(
            is_available=True, last_active_at__lt=inactive_before
        ).update(is_available=False)

    for reveal in RevealRequest.objects.filter(
        status=RevealRequest.Status.WINDOW_OPEN,
        window_expires_at__lte=now,
    ):
        complete_reveal(reveal, now=now)
    return expired_request_count


def _open_window(reveal, now):
    """Transition a RevealRequest into WINDOW_OPEN and persist it."""
    reveal.status = RevealRequest.Status.WINDOW_OPEN
    reveal.window_opened_at = now
    reveal.window_expires_at = now + REVEAL_WINDOW
    fields = ["status", "window_opened_at", "window_expires_at"]
    # Only write partner_responded_at if it was set by the caller.
    if reveal.partner_responded_at:
        fields.append("partner_responded_at")
    reveal.save(update_fields=fields)
    return reveal


@transaction.atomic
def start_reveal(connection, requester, now=None):
    now = now or timezone.now()
    connection = Connection.objects.select_for_update().get(pk=connection.pk)
    if connection.status != Connection.Status.ACTIVE or not connection.includes(requester):
        raise PermissionDenied
    if connection.identities_revealed:
        raise ValidationError("Identities are already revealed.")
    if connection.reveal_cooldown_until and connection.reveal_cooldown_until > now:
        raise ValidationError("Reveal requests are cooling down after a recent rejection.")
    if connection.reveal_requests.filter(
        status__in=[
            RevealRequest.Status.PENDING_PARTNER,
            RevealRequest.Status.AWAITING_WOMAN,
            RevealRequest.Status.WINDOW_OPEN,
        ]
    ).exists():
        raise ValidationError("A reveal request is already in progress.")
    return RevealRequest.objects.create(connection=connection, requester=requester)


@transaction.atomic
def partner_respond(reveal, responder, accept, now=None):
    now = now or timezone.now()
    # ── BUG FIX (MEDIUM) ─────────────────────────────────────────────────────
    # Original select_related only included "connection"; accessing
    # reveal.requester.gender caused an additional DB round-trip.
    # Added "requester" to the select_related call.
    reveal = (
        RevealRequest.objects.select_for_update()
        .select_related("connection", "requester")
        .get(pk=reveal.pk)
    )
    if reveal.status != RevealRequest.Status.PENDING_PARTNER:
        raise ValidationError("This reveal request is no longer waiting for a response.")
    if responder.pk == reveal.requester_id or not reveal.connection.includes(responder):
        raise PermissionDenied
    reveal.partner_responded_at = now
    if not accept:
        return reject_reveal(reveal, now=now)
    if reveal.requester.gender == User.Gender.MAN:
        # Man initiated → woman accepted → open the photo window immediately.
        return _open_window(reveal, now)
    # Woman initiated → man accepted → ask the woman to reconfirm she is ready
    # before the photo window opens (safety step when she may not be on screen).
    reveal.status = RevealRequest.Status.AWAITING_WOMAN
    reveal.save(update_fields=["status", "partner_responded_at"])
    return reveal


@transaction.atomic
def woman_reconfirm(reveal, woman, now=None):
    now = now or timezone.now()
    reveal = (
        RevealRequest.objects.select_for_update()
        .select_related("connection")
        .get(pk=reveal.pk)
    )
    if reveal.status != RevealRequest.Status.AWAITING_WOMAN or woman.pk != reveal.connection.woman_id:
        raise PermissionDenied
    return _open_window(reveal, now)


@transaction.atomic
def complete_reveal(reveal, woman=None, now=None):
    """Finalise a reveal — either triggered by the woman confirming or by the
    3-minute window auto-expiring (process_timers calls this with woman=None)."""
    now = now or timezone.now()
    reveal = (
        RevealRequest.objects.select_for_update()
        .select_related("connection")
        .get(pk=reveal.pk)
    )
    if reveal.status != RevealRequest.Status.WINDOW_OPEN:
        raise ValidationError("The reveal photo window is not open.")
    if woman is not None and woman.pk != reveal.connection.woman_id:
        raise PermissionDenied
    reveal.status = RevealRequest.Status.REVEALED
    reveal.resolved_at = now
    reveal.save(update_fields=["status", "resolved_at"])
    connection = reveal.connection
    connection.identities_revealed = True
    connection.revealed_at = now
    connection.save(update_fields=["identities_revealed", "revealed_at"])
    return reveal


@transaction.atomic
def reject_reveal(reveal, now=None):
    """Partner declined the reveal request before the photo window opened.
    Sets status=REJECTED and starts the 24-hour reveal cooldown."""
    now = now or timezone.now()
    reveal.status = RevealRequest.Status.REJECTED
    reveal.resolved_at = now
    # Only include partner_responded_at if it was set by the caller (partner_respond).
    fields = ["status", "resolved_at"]
    if reveal.partner_responded_at:
        fields.append("partner_responded_at")
    reveal.save(update_fields=fields)
    connection = reveal.connection
    connection.reveal_cooldown_until = now + DAY
    connection.save(update_fields=["reveal_cooldown_until"])
    return reveal


# ── BUG FIX (MEDIUM) ─────────────────────────────────────────────────────────
# The original code used reject_reveal (status=REJECTED) for two different
# actions:
#   1. Partner declining before the photo window (partner_respond accept=False)
#   2. Woman cancelling during the 3-minute photo preview window (reveal_decide)
#
# These are semantically distinct events. The model already defined a
# CANCELLED status for case 2 but it was never used. This function handles
# that case correctly. The view (reveal_decide) now calls cancel_reveal instead
# of reject_reveal for window cancellations.
@transaction.atomic
def cancel_reveal(reveal, woman, now=None):
    """Woman cancels during the 3-minute photo preview window.
    Sets status=CANCELLED and starts the 24-hour reveal cooldown."""
    now = now or timezone.now()
    reveal = (
        RevealRequest.objects.select_for_update()
        .select_related("connection")
        .get(pk=reveal.pk)
    )
    if reveal.status != RevealRequest.Status.WINDOW_OPEN:
        raise ValidationError("The photo window is not open.")
    if woman.pk != reveal.connection.woman_id:
        raise PermissionDenied
    reveal.status = RevealRequest.Status.CANCELLED
    reveal.resolved_at = now
    reveal.save(update_fields=["status", "resolved_at"])
    connection = reveal.connection
    connection.reveal_cooldown_until = now + DAY
    connection.save(update_fields=["reveal_cooldown_until"])
    return reveal


@transaction.atomic
def report_user(reporter, connection, reason, severity):
    if not connection.includes(reporter):
        raise PermissionDenied
    reported_user = connection.other_user(reporter)
    report = Report.objects.create(
        reporter=reporter,
        reported_user=reported_user,
        connection=connection,
        reason=reason,
        severity=severity,
    )
    if severity == Report.Severity.SEVERE:
        # Immediate suspension pending admin review. is_blocked prevents new
        # matches; suspended_until gates further platform actions.
        reported_user.is_blocked = True
        reported_user.suspended_until = timezone.now() + timedelta(hours=48)
        reported_user.save(update_fields=["is_blocked", "suspended_until"])
    else:
        distinct_reports = (
            Report.objects.filter(
                reported_user=reported_user,
                status=Report.Status.OPEN,
                reporter__email_verified=True,
            )
            .values("reporter")
            .distinct()
            .count()
        )
        if distinct_reports >= 4:
            reported_user.suspended_until = timezone.now() + timedelta(hours=48)
            reported_user.save(update_fields=["suspended_until"])
    return report
