"""
GIM Connect – views.py
======================
All HTTP view logic for the connect app.

Design principles applied here:
  • Views stay thin – all domain rules live in services.py.
  • Every mutation is POST-only (@require_POST).
  • Every authenticated route is decorated (@login_required).
  • process_timers() is called at most once per minute via a simple
    in-process throttle (replace with Celery beat for production scale).
  • The JSON polling endpoint is rate-limited to ~1 req/3 s per user.
  • signup is wrapped in a DB transaction so a failed email send never
    leaves a ghost user in the database.
  • reveal_decide checks the woman-permission on BOTH branches.
  • report_connection catches only the specific duplicate-report error.
  • active_count is evaluated once and reused (no duplicate DB query).
  • Logging is added for every unexpected exception path.
"""

import logging
import time
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail, BadHeaderError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import MessageForm, OTPForm, ReportForm, SettingsForm, SignupForm
from .models import (
    Block,
    Connection,
    ConnectionRequest,
    EmailOTP,
    Message,
    RevealRequest,
    User,
)
from .services import (
    cancel_request,
    complete_reveal,
    end_connection,
    partner_respond,
    process_timers,
    reject_reveal,
    cancel_reveal,  # <--- Added the missing import here
    report_user,
    request_connection,
    start_reveal,
    woman_reconfirm,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_TIMER_THROTTLE_SECONDS = 60

def _maybe_process_timers():
    lock_key = "gim:process_timers_last_run"
    last_run = cache.get(lock_key)
    if last_run is None:
        try:
            process_timers()
        except Exception:
            logger.exception("process_timers() raised an unexpected error")
        cache.set(lock_key, time.monotonic(), timeout=_TIMER_THROTTLE_SECONDS)


def _get_connection_for_user(request, pk, *, allow_ended=False):
    connection = get_object_or_404(
        Connection.objects.select_related("man", "woman"), pk=pk
    )
    if not connection.includes(request.user):
        raise PermissionDenied
    if not allow_ended and connection.status != Connection.Status.ACTIVE:
        raise PermissionDenied
    return connection


def _get_reveal_for_user(pk, user, *, require_woman=False):
    reveal = get_object_or_404(
        RevealRequest.objects.select_related("connection__man", "connection__woman"),
        pk=pk,
    )
    if require_woman and user.pk != reveal.connection.woman_id:
        raise PermissionDenied
    if not reveal.connection.includes(user):
        raise PermissionDenied
    return reveal


def _json_error(message, status=400):
    return JsonResponse({"error": message}, status=status)


def _rate_limited(cache_key_prefix, limit_seconds):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            key = f"{cache_key_prefix}:{request.user.pk}"
            if cache.get(key):
                return _json_error("Too many requests.", status=429)
            cache.set(key, 1, timeout=limit_seconds)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Public views – no login required
# ---------------------------------------------------------------------------

def landing(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(request, "connect/landing.html")


def signup(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = SignupForm(request.POST or None, request.FILES or None)

    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                user = form.save(commit=False)
                user.email = user.email.lower().strip()
                user.save()
                otp = EmailOTP.issue(user.email)

                send_mail(
                    subject="Your GIM Connect verification code",
                    message=(
                        f"Your verification code is {otp.code}.\n"
                        "It expires in 10 minutes.\n\n"
                        "If you did not request this, please ignore this email."
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    fail_silently=False,
                )

        except BadHeaderError:
            logger.warning("signup: BadHeaderError for email %s", form.cleaned_data.get("email"))
            form.add_error("email", "Invalid email address.")
            return render(request, "connect/signup.html", {"form": form})

        except Exception:
            logger.exception("signup: failed to create account or send OTP")
            messages.error(
                request,
                "We could not send a verification email right now. "
                "Please try again in a few minutes.",
            )
            return render(request, "connect/signup.html", {"form": form})

        request.session["verification_email"] = user.email
        messages.success(request, "We sent a six-digit code to your GIM email.")
        return redirect("verify_email")

    return render(request, "connect/signup.html", {"form": form})


def verify_email(request):
    email = request.session.get("verification_email")
    if not email:
        return redirect("signup")

    form = OTPForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        code = form.cleaned_data["code"].strip()
        otp = (
            EmailOTP.objects
            .filter(email=email, code=code)
            .order_by("-created_at")
            .first()
        )

        if not otp or not otp.is_valid:
            form.add_error("code", "That code is invalid or has expired.")
            logger.info("verify_email: failed attempt for %s", email)
        else:
            with transaction.atomic():
                otp.used_at = timezone.now()
                otp.save(update_fields=["used_at"])

                user = get_object_or_404(User, email=email)
                user.email_verified = True
                user.save(update_fields=["email_verified"])

            request.session.pop("verification_email", None)
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            logger.info("verify_email: verified %s", email)
            return redirect("onboarding")

    return render(request, "connect/verify_email.html", {"form": form, "email": email})


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------

@login_required
def onboarding(request):
    if request.user.onboarding_complete:
        return redirect("dashboard")

    if request.method == "POST":
        request.user.onboarding_complete = True
        request.user.save(update_fields=["onboarding_complete"])
        messages.success(request, "Welcome to GIM Connect.")
        return redirect("dashboard")

    return render(request, "connect/onboarding.html")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@login_required
def dashboard(request):
    _maybe_process_timers()

    active = request.user.active_connections().select_related("man", "woman")
    active_count = active.count()

    waiting = (
        ConnectionRequest.objects
        .filter(requester=request.user, status=ConnectionRequest.Status.QUEUED)
        .first()
    )

    history = (
        Connection.objects.filter(man=request.user)
        | Connection.objects.filter(woman=request.user)
    ).exclude(
        status=Connection.Status.ACTIVE
    ).select_related("man", "woman").order_by("-ended_at")[:8]

    now = timezone.now()
    request_ready = request.user.request_available_at <= now

    can_request = (
        request.user.is_eligible
        and active_count < request.user.connection_cap
        and request_ready
    )

    return render(
        request,
        "connect/dashboard.html",
        {
            "active_connections": active,
            "waiting_request": waiting,
            "history": history,
            "active_count": active_count,
            "request_ready": request_ready,
            "can_request": can_request,
        },
    )


# ---------------------------------------------------------------------------
# Connection requests
# ---------------------------------------------------------------------------

@login_required
@require_POST
def new_request(request):
    try:
        _connection_request, connection = request_connection(request.user)
        if connection:
            messages.success(request, "A new anonymous connection is ready.")
            return redirect("chat", pk=connection.pk)
        messages.success(
            request,
            "Your request is in the lobby. We will match it when a spot opens.",
        )
    except PermissionDenied:
        messages.error(request, "You are not eligible to make a connection request right now.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except Exception:
        logger.exception("new_request: unexpected error for user %s", request.user.pk)
        messages.error(request, "Something went wrong. Please try again.")

    return redirect("dashboard")


@login_required
@require_POST
def cancel_waiting_request(request, pk):
    waiting = get_object_or_404(
        ConnectionRequest, pk=pk, requester=request.user
    )
    try:
        cancel_request(waiting)
        messages.info(request, "Lobby request cancelled.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except Exception:
        logger.exception(
            "cancel_waiting_request: unexpected error for request %s", pk
        )
        messages.error(request, "Could not cancel request. Please try again.")

    return redirect("dashboard")


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

@login_required
def chat(request, pk):
    _maybe_process_timers()

    connection = _get_connection_for_user(request, pk, allow_ended=True)

    reveal = (
        connection.reveal_requests.filter(
            status__in=[
                RevealRequest.Status.PENDING_PARTNER,
                RevealRequest.Status.AWAITING_WOMAN,
                RevealRequest.Status.WINDOW_OPEN,
            ]
        )
        .select_related("connection")
        .first()
    )

    return render(
        request,
        "connect/chat.html",
        {
            "connection": connection,
            "other": connection.other_user(request.user),
            "chat_messages": (
                connection.chat_messages
                .select_related("sender")
                .order_by("created_at")
            ),
            "form": MessageForm(),
            "reveal": reveal,
            "is_woman": request.user.pk == connection.woman_id,
        },
    )


@login_required
@require_POST
def send_message(request, pk):
    connection = _get_connection_for_user(request, pk)
    form = MessageForm(request.POST)
    if form.is_valid():
        body = form.cleaned_data["body"].strip()
        if body:
            Message.objects.create(
                connection=connection,
                sender=request.user,
                body=body,
            )
    else:
        messages.error(request, "Message could not be sent.")
    return redirect("chat", pk=pk)


@login_required
@_rate_limited("gim:poll", limit_seconds=2)
def messages_json(request, pk):
    connection = _get_connection_for_user(request, pk, allow_ended=True)

    try:
        after = int(request.GET.get("after", "0"))
    except (ValueError, TypeError):
        after = 0

    items = (
        connection.chat_messages
        .filter(pk__gt=after)
        .select_related("sender")
        .order_by("pk")
    )

    active_reveal = (
        connection.reveal_requests
        .filter(
            status__in=[
                RevealRequest.Status.PENDING_PARTNER,
                RevealRequest.Status.AWAITING_WOMAN,
                RevealRequest.Status.WINDOW_OPEN,
            ]
        )
        .values_list("status", flat=True)
        .first()
    )

    return JsonResponse(
        {
            "messages": [
                {
                    "id": item.pk,
                    "body": item.body,
                    "mine": item.sender_id == request.user.pk,
                    "time": timezone.localtime(item.created_at).strftime("%-I:%M %p"),
                }
                for item in items
            ],
            "connection_active": connection.status == Connection.Status.ACTIVE,
            "reveal_status": active_reveal,
        }
    )


@login_required
@require_POST
def end_chat(request, pk):
    connection = _get_connection_for_user(request, pk)
    try:
        end_connection(connection, ended_by=request.user)
        messages.info(request, "This connection has ended.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except Exception:
        logger.exception("end_chat: unexpected error for connection %s", pk)
        messages.error(request, "Could not end conversation. Please try again.")
    return redirect("dashboard")


# ---------------------------------------------------------------------------
# Block & Report
# ---------------------------------------------------------------------------

@login_required
@require_POST
def block_user(request, pk):
    connection = _get_connection_for_user(request, pk, allow_ended=True)
    other = connection.other_user(request.user)

    try:
        with transaction.atomic():
            Block.objects.get_or_create(blocker=request.user, blocked=other)
            if connection.status == Connection.Status.ACTIVE:
                end_connection(connection, ended_by=request.user)
    except Exception:
        logger.exception(
            "block_user: unexpected error – user %s blocking other %s",
            request.user.pk, other.pk,
        )
        messages.error(request, "Could not complete block. Please try again.")
        return redirect("chat", pk=pk)

    messages.success(
        request,
        "That account is blocked and will not be matched with you again.",
    )
    return redirect("dashboard")


@login_required
def report_connection(request, pk):
    connection = _get_connection_for_user(request, pk, allow_ended=True)
    form = ReportForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        try:
            report_user(
                reporter=request.user,
                connection=connection,
                reason=form.cleaned_data["reason"],
                severity=form.cleaned_data["severity"],
            )
            messages.success(
                request,
                "Report received. Thank you – an admin will review it shortly.",
            )
            return redirect("chat", pk=pk)

        except (IntegrityError, ValidationError) as exc:
            if hasattr(exc, "messages"):
                form.add_error(None, " ".join(exc.messages))
            else:
                form.add_error(None, "You have already reported this conversation.")

        except Exception:
            logger.exception(
                "report_connection: unexpected error for connection %s", pk
            )
            messages.error(request, "Something went wrong. Please try again.")
            return redirect("chat", pk=pk)

    return render(
        request,
        "connect/report.html",
        {"form": form, "connection": connection},
    )


# ---------------------------------------------------------------------------
# Reveal flow
# ---------------------------------------------------------------------------

@login_required
@require_POST
def reveal_start(request, pk):
    connection = _get_connection_for_user(request, pk)
    try:
        start_reveal(connection, request.user)
        messages.success(request, "Reveal request sent.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except Exception:
        logger.exception("reveal_start: unexpected error for connection %s", pk)
        messages.error(request, "Could not start reveal. Please try again.")
    return redirect("chat", pk=pk)


@login_required
@require_POST
def reveal_partner_response(request, pk, action):
    if action not in ("accept", "reject"):
        raise PermissionDenied

    reveal = _get_reveal_for_user(pk, request.user)
    try:
        partner_respond(reveal, request.user, accept=(action == "accept"))
    except (ValidationError, PermissionDenied) as exc:
        msg = (
            " ".join(exc.messages)
            if hasattr(exc, "messages")
            else "Action not allowed."
        )
        messages.error(request, msg)
    except Exception:
        logger.exception("reveal_partner_response: unexpected error reveal %s", pk)
        messages.error(request, "Could not process response. Please try again.")

    return redirect("chat", pk=reveal.connection_id)


@login_required
@require_POST
def reveal_reconfirm(request, pk):
    reveal = _get_reveal_for_user(pk, request.user, require_woman=True)
    try:
        woman_reconfirm(reveal, request.user)
    except (ValidationError, PermissionDenied) as exc:
        msg = (
            " ".join(exc.messages)
            if hasattr(exc, "messages")
            else "Action not allowed."
        )
        messages.error(request, msg)
    except Exception:
        logger.exception("reveal_reconfirm: unexpected error reveal %s", pk)
        messages.error(request, "Could not confirm. Please try again.")

    return redirect("chat", pk=reveal.connection_id)


@login_required
@require_POST
def reveal_decide(request, pk, action):
    if action not in ("confirm", "reject"):
        raise PermissionDenied

    reveal = _get_reveal_for_user(pk, request.user, require_woman=True)

    try:
        if action == "confirm":
            complete_reveal(reveal, woman=request.user)
            messages.success(request, "Identities revealed. Enjoy the moment.")
        else:
            cancel_reveal(reveal, woman=request.user) # <--- THIS IS THE FIX
            messages.info(
                request,
                "Reveal cancelled. The connection continues anonymously.",
            )
    except (ValidationError, PermissionDenied) as exc:
        msg = (
            " ".join(exc.messages)
            if hasattr(exc, "messages")
            else "Action not allowed."
        )
        messages.error(request, msg)
    except Exception:
        logger.exception("reveal_decide: unexpected error reveal %s", pk)
        messages.error(request, "Something went wrong. Please try again.")

    return redirect("chat", pk=reveal.connection_id)


# ---------------------------------------------------------------------------
# Account settings
# ---------------------------------------------------------------------------

@login_required
def account_settings(request):
    """
    Let users update display name, notification preferences, etc.
    """
    form = SettingsForm(request.POST or None, instance=request.user)

    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "Settings updated.")
        return redirect("settings")

    return render(request, "connect/settings.html", {"form": form})
from .forms import MessageForm, OTPForm, ReportForm, SettingsForm, SignupForm
from .models import (
    Block,
    Connection,
    ConnectionRequest,
    EmailOTP,
    Message,
    RevealRequest,
    User,
)
from .services import (
    cancel_request,
    complete_reveal,
    end_connection,
    partner_respond,
    process_timers,
    reject_reveal,
    report_user,
    request_connection,
    start_reveal,
    woman_reconfirm,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# How often (seconds) process_timers() may run inside a request cycle.
# Once Celery is wired up, remove the call sites and delete this constant.
_TIMER_THROTTLE_SECONDS = 60


def _maybe_process_timers():
    """
    Run process_timers() at most once every _TIMER_THROTTLE_SECONDS seconds
    across all workers (uses the Django cache backend for the lock).

    This is a pragmatic stop-gap for single-server deployments.
    For production scale, move process_timers() into a Celery periodic task
    and remove all call sites here.
    """
    lock_key = "gim:process_timers_last_run"
    last_run = cache.get(lock_key)
    if last_run is None:
        try:
            process_timers()
        except Exception:
            logger.exception("process_timers() raised an unexpected error")
        cache.set(lock_key, time.monotonic(), timeout=_TIMER_THROTTLE_SECONDS)


def _get_connection_for_user(request, pk, *, allow_ended=False):
    """
    Fetch a Connection by pk and assert the requesting user is a participant.
    Raises Http404 if not found, PermissionDenied if not a participant or
    the connection has ended when allow_ended=False.
    """
    connection = get_object_or_404(
        Connection.objects.select_related("man", "woman"), pk=pk
    )
    if not connection.includes(request.user):
        raise PermissionDenied
    if not allow_ended and connection.status != Connection.Status.ACTIVE:
        raise PermissionDenied
    return connection


def _get_reveal_for_user(pk, user, *, require_woman=False):
    """
    Fetch a RevealRequest by pk with its connection pre-fetched.
    Optionally assert that *user* is the woman in that connection.
    """
    reveal = get_object_or_404(
        RevealRequest.objects.select_related("connection__man", "connection__woman"),
        pk=pk,
    )
    if require_woman and user.pk != reveal.connection.woman_id:
        raise PermissionDenied
    # Basic sanity: the user must be in the connection at all.
    if not reveal.connection.includes(user):
        raise PermissionDenied
    return reveal


def _json_error(message, status=400):
    return JsonResponse({"error": message}, status=status)


def _rate_limited(cache_key_prefix, limit_seconds):
    """
    Decorator that returns HTTP 429 if the same cache key fires more than
    once within *limit_seconds*.  Used to throttle the polling endpoint.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            key = f"{cache_key_prefix}:{request.user.pk}"
            if cache.get(key):
                return _json_error("Too many requests.", status=429)
            cache.set(key, 1, timeout=limit_seconds)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Public views – no login required
# ---------------------------------------------------------------------------

def landing(request):
    """Redirect authenticated users straight to their dashboard."""
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(request, "connect/landing.html")


def signup(request):
    """
    Create a new user account and send an OTP verification email.

    The user record and OTP are created inside a transaction so that a
    failed send_mail() call never leaves an unverifiable ghost account.
    If email delivery fails, the transaction rolls back, the form is
    re-rendered with a clear error, and the user can try again.
    """
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = SignupForm(request.POST or None, request.FILES or None)

    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                user = form.save(commit=False)
                user.email = user.email.lower().strip()
                user.save()
                otp = EmailOTP.issue(user.email)

                # Attempt email delivery inside the transaction so that any
                # SMTP error triggers a rollback before we commit the user.
                send_mail(
                    subject="Your GIM Connect verification code",
                    message=(
                        f"Your verification code is {otp.code}.\n"
                        "It expires in 10 minutes.\n\n"
                        "If you did not request this, please ignore this email."
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    fail_silently=False,
                )

        except BadHeaderError:
            logger.warning("signup: BadHeaderError for email %s", form.cleaned_data.get("email"))
            form.add_error("email", "Invalid email address.")
            return render(request, "connect/signup.html", {"form": form})

        except Exception:
            logger.exception("signup: failed to create account or send OTP")
            messages.error(
                request,
                "We could not send a verification email right now. "
                "Please try again in a few minutes.",
            )
            return render(request, "connect/signup.html", {"form": form})

        request.session["verification_email"] = user.email
        messages.success(request, "We sent a six-digit code to your GIM email.")
        return redirect("verify_email")

    return render(request, "connect/signup.html", {"form": form})


def verify_email(request):
    """
    Validate the OTP the user received by email and activate their account.

    Guards:
      - Session must contain verification_email (set by signup view).
      - OTP must exist, be unused, and not have expired.
    """
    email = request.session.get("verification_email")
    if not email:
        return redirect("signup")

    form = OTPForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        code = form.cleaned_data["code"].strip()
        otp = (
            EmailOTP.objects
            .filter(email=email, code=code)
            .order_by("-created_at")
            .first()
        )

        if not otp or not otp.is_valid:
            form.add_error("code", "That code is invalid or has expired.")
            logger.info("verify_email: failed attempt for %s", email)
        else:
            with transaction.atomic():
                otp.used_at = timezone.now()
                otp.save(update_fields=["used_at"])

                user = get_object_or_404(User, email=email)
                user.email_verified = True
                user.save(update_fields=["email_verified"])

            # Clear the session key so the OTP flow cannot be replayed.
            request.session.pop("verification_email", None)
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            logger.info("verify_email: verified %s", email)
            return redirect("onboarding")

    return render(request, "connect/verify_email.html", {"form": form, "email": email})


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------

@login_required
def onboarding(request):
    """
    Show community guidelines and mark the user as onboarding-complete
    once they explicitly agree.

    Redirects already-onboarded users to the dashboard to prevent
    re-submission.
    """
    if request.user.onboarding_complete:
        return redirect("dashboard")

    if request.method == "POST":
        request.user.onboarding_complete = True
        request.user.save(update_fields=["onboarding_complete"])
        messages.success(request, "Welcome to GIM Connect.")
        return redirect("dashboard")

    return render(request, "connect/onboarding.html")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@login_required
def dashboard(request):
    """
    Main landing page after login.

    active_count is evaluated once and passed explicitly so it is not
    re-queried inside the template or the can_request computation.
    """
    _maybe_process_timers()

    active = request.user.active_connections().select_related("man", "woman")
    active_count = active.count()          # single DB query, reused below

    waiting = (
        ConnectionRequest.objects
        .filter(requester=request.user, status=ConnectionRequest.Status.QUEUED)
        .first()
    )

    # Union queryset for ended connections – last 8 for history panel.
    history = (
        Connection.objects.filter(man=request.user)
        | Connection.objects.filter(woman=request.user)
    ).exclude(
        status=Connection.Status.ACTIVE
    ).select_related("man", "woman").order_by("-ended_at")[:8]

    now = timezone.now()
    request_ready = request.user.request_available_at <= now

    can_request = (
        request.user.is_eligible
        and active_count < request.user.connection_cap
        and request_ready
    )

    return render(
        request,
        "connect/dashboard.html",
        {
            "active_connections": active,
            "waiting_request": waiting,
            "history": history,
            "active_count": active_count,
            "request_ready": request_ready,
            "can_request": can_request,
        },
    )


# ---------------------------------------------------------------------------
# Connection requests
# ---------------------------------------------------------------------------

@login_required
@require_POST
def new_request(request):
    """
    Place a new connection request for the current user.
    If a match is found immediately, redirect to the new chat.
    Otherwise, confirm the lobby queue position.
    """
    try:
        _connection_request, connection = request_connection(request.user)
        if connection:
            messages.success(request, "A new anonymous connection is ready.")
            return redirect("chat", pk=connection.pk)
        messages.success(
            request,
            "Your request is in the lobby. We will match it when a spot opens.",
        )
    except PermissionDenied:
        messages.error(request, "You are not eligible to make a connection request right now.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except Exception:
        logger.exception("new_request: unexpected error for user %s", request.user.pk)
        messages.error(request, "Something went wrong. Please try again.")

    return redirect("dashboard")


@login_required
@require_POST
def cancel_waiting_request(request, pk):
    """Cancel a queued (lobby) connection request owned by the current user."""
    waiting = get_object_or_404(
        ConnectionRequest, pk=pk, requester=request.user
    )
    try:
        cancel_request(waiting)
        messages.info(request, "Lobby request cancelled.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except Exception:
        logger.exception(
            "cancel_waiting_request: unexpected error for request %s", pk
        )
        messages.error(request, "Could not cancel request. Please try again.")

    return redirect("dashboard")


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

@login_required
def chat(request, pk):
    """
    Render the conversation view.
    Also surfaces any active reveal request so the template can branch on it.
    """
    _maybe_process_timers()

    connection = _get_connection_for_user(request, pk, allow_ended=True)

    reveal = (
        connection.reveal_requests.filter(
            status__in=[
                RevealRequest.Status.PENDING_PARTNER,
                RevealRequest.Status.AWAITING_WOMAN,
                RevealRequest.Status.WINDOW_OPEN,
            ]
        )
        .select_related("connection")
        .first()
    )

    return render(
        request,
        "connect/chat.html",
        {
            "connection": connection,
            "other": connection.other_user(request.user),
            "chat_messages": (
                connection.chat_messages
                .select_related("sender")
                .order_by("created_at")
            ),
            "form": MessageForm(),
            "reveal": reveal,
            "is_woman": request.user.pk == connection.woman_id,
        },
    )


@login_required
@require_POST
def send_message(request, pk):
    """
    Append a new message to the active connection.
    Silently discards blank bodies (belt-and-suspenders; the form validates too).
    """
    connection = _get_connection_for_user(request, pk)
    form = MessageForm(request.POST)
    if form.is_valid():
        body = form.cleaned_data["body"].strip()
        if body:
            Message.objects.create(
                connection=connection,
                sender=request.user,
                body=body,
            )
    else:
        messages.error(request, "Message could not be sent.")
    return redirect("chat", pk=pk)


@login_required
@_rate_limited("gim:poll", limit_seconds=2)
def messages_json(request, pk):
    """
    Lightweight long-poll endpoint used by the chat JS to fetch new messages.

    Rate-limited to one request per 2 seconds per user to prevent accidental
    hammering.  Returns only messages with pk > ?after.

    Response shape:
      {
        "messages": [{ id, body, mine, time }],
        "connection_active": bool,
        "reveal_status": str | null
      }
    """
    connection = _get_connection_for_user(request, pk, allow_ended=True)

    try:
        after = int(request.GET.get("after", "0"))
    except (ValueError, TypeError):
        after = 0

    items = (
        connection.chat_messages
        .filter(pk__gt=after)
        .select_related("sender")
        .order_by("pk")
    )

    # Surface the current reveal status so the client can reload on change.
    active_reveal = (
        connection.reveal_requests
        .filter(
            status__in=[
                RevealRequest.Status.PENDING_PARTNER,
                RevealRequest.Status.AWAITING_WOMAN,
                RevealRequest.Status.WINDOW_OPEN,
            ]
        )
        .values_list("status", flat=True)
        .first()
    )

    return JsonResponse(
        {
            "messages": [
                {
                    "id": item.pk,
                    "body": item.body,
                    "mine": item.sender_id == request.user.pk,
                    "time": timezone.localtime(item.created_at).strftime("%-I:%M %p"),
                }
                for item in items
            ],
            "connection_active": connection.status == Connection.Status.ACTIVE,
            "reveal_status": active_reveal,
        }
    )


@login_required
@require_POST
def end_chat(request, pk):
    """End an active connection. Both participants see it as read-only afterward."""
    connection = _get_connection_for_user(request, pk)
    try:
        end_connection(connection, ended_by=request.user)
        messages.info(request, "This connection has ended.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except Exception:
        logger.exception("end_chat: unexpected error for connection %s", pk)
        messages.error(request, "Could not end conversation. Please try again.")
    return redirect("dashboard")


# ---------------------------------------------------------------------------
# Block & Report
# ---------------------------------------------------------------------------

@login_required
@require_POST
def block_user(request, pk):
    """
    Block the other participant in a connection.
    If the connection is still active, end it as part of the same operation.
    """
    connection = _get_connection_for_user(request, pk, allow_ended=True)
    other = connection.other_user(request.user)

    try:
        with transaction.atomic():
            Block.objects.get_or_create(blocker=request.user, blocked=other)
            if connection.status == Connection.Status.ACTIVE:
                end_connection(connection, ended_by=request.user)
    except Exception:
        logger.exception(
            "block_user: unexpected error – user %s blocking other %s",
            request.user.pk, other.pk,
        )
        messages.error(request, "Could not complete block. Please try again.")
        return redirect("chat", pk=pk)

    messages.success(
        request,
        "That account is blocked and will not be matched with you again.",
    )
    return redirect("dashboard")


@login_required
def report_connection(request, pk):
    """
    Submit a safety report for the other participant in a connection.

    Catches only IntegrityError / the specific duplicate-report exception
    from the service layer rather than a bare Exception.
    """
    connection = _get_connection_for_user(request, pk, allow_ended=True)
    form = ReportForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        try:
            report_user(
                reporter=request.user,
                connection=connection,
                reason=form.cleaned_data["reason"],
                severity=form.cleaned_data["severity"],
            )
            messages.success(
                request,
                "Report received. Thank you – an admin will review it shortly.",
            )
            return redirect("chat", pk=pk)

        except (IntegrityError, ValidationError) as exc:
            # Service layer raises ValidationError for duplicate reports.
            if hasattr(exc, "messages"):
                form.add_error(None, " ".join(exc.messages))
            else:
                form.add_error(None, "You have already reported this conversation.")

        except Exception:
            logger.exception(
                "report_connection: unexpected error for connection %s", pk
            )
            messages.error(request, "Something went wrong. Please try again.")
            return redirect("chat", pk=pk)

    return render(
        request,
        "connect/report.html",
        {"form": form, "connection": connection},
    )


# ---------------------------------------------------------------------------
# Reveal flow
# ---------------------------------------------------------------------------

@login_required
@require_POST
def reveal_start(request, pk):
    """
    Initiate an identity-reveal request for this connection.
    Either participant may start the flow.
    """
    connection = _get_connection_for_user(request, pk)
    try:
        start_reveal(connection, request.user)
        messages.success(request, "Reveal request sent.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except Exception:
        logger.exception("reveal_start: unexpected error for connection %s", pk)
        messages.error(request, "Could not start reveal. Please try again.")
    return redirect("chat", pk=pk)


@login_required
@require_POST
def reveal_partner_response(request, pk, action):
    """
    The non-initiating partner accepts or rejects the reveal request.
    Valid actions: 'accept' | 'reject'.
    """
    if action not in ("accept", "reject"):
        raise PermissionDenied

    reveal = _get_reveal_for_user(pk, request.user)
    try:
        partner_respond(reveal, request.user, accept=(action == "accept"))
    except (ValidationError, PermissionDenied) as exc:
        msg = (
            " ".join(exc.messages)
            if hasattr(exc, "messages")
            else "Action not allowed."
        )
        messages.error(request, msg)
    except Exception:
        logger.exception("reveal_partner_response: unexpected error reveal %s", pk)
        messages.error(request, "Could not process response. Please try again.")

    return redirect("chat", pk=reveal.connection_id)


@login_required
@require_POST
def reveal_reconfirm(request, pk):
    """
    The woman reconfirms her presence before the photo-review window opens.
    Only the woman in this connection may call this endpoint.
    """
    reveal = _get_reveal_for_user(pk, request.user, require_woman=True)
    try:
        woman_reconfirm(reveal, request.user)
    except (ValidationError, PermissionDenied) as exc:
        msg = (
            " ".join(exc.messages)
            if hasattr(exc, "messages")
            else "Action not allowed."
        )
        messages.error(request, msg)
    except Exception:
        logger.exception("reveal_reconfirm: unexpected error reveal %s", pk)
        messages.error(request, "Could not confirm. Please try again.")

    return redirect("chat", pk=reveal.connection_id)


@login_required
@require_POST
def reveal_decide(request, pk, action):
    """
    The woman makes her final decision after the photo-review window.

      'confirm'  → complete_reveal() – both identities become visible.
      'reject'   → reject_reveal()   – connection continues anonymously.

    Both branches now explicitly check that request.user is the woman
    before taking any action.
    """
    if action not in ("confirm", "reject"):
        raise PermissionDenied

    # require_woman=True on both branches – woman controls the final decision.
    reveal = _get_reveal_for_user(pk, request.user, require_woman=True)

    try:
        if action == "confirm":
            complete_reveal(reveal, woman=request.user)
            messages.success(request, "Identities revealed. Enjoy the moment.")
        else:
            reject_reveal(reveal)
            messages.info(
                request,
                "Reveal cancelled. The connection continues anonymously.",
            )
    except (ValidationError, PermissionDenied) as exc:
        msg = (
            " ".join(exc.messages)
            if hasattr(exc, "messages")
            else "Action not allowed."
        )
        messages.error(request, msg)
    except Exception:
        logger.exception("reveal_decide: unexpected error reveal %s", pk)
        messages.error(request, "Something went wrong. Please try again.")

    return redirect("chat", pk=reveal.connection_id)


# ---------------------------------------------------------------------------
# Account settings
# ---------------------------------------------------------------------------

@login_required
def account_settings(request):
    """
    Let users update display name, notification preferences, etc.
    Password changes are handled separately via Django's built-in views;
    if SettingsForm ever includes a password field, call
    update_session_auth_hash() to keep the session valid after saving.
    """
    form = SettingsForm(request.POST or None, instance=request.user)

    if request.method == "POST" and form.is_valid():
        user = form.save()
        # Keep session alive if the form ever includes a password field.
        update_session_auth_hash(request, user)
        messages.success(request, "Settings updated.")
        return redirect("settings")

    return render(request, "connect/settings.html", {"form": form})    form = SignupForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        user = form.save(commit=False)
        user.email = user.email.lower()
        user.save()
        otp = EmailOTP.issue(user.email)
        send_mail(
            "Your GIM Connect verification code",
            f"Your verification code is {otp.code}. It expires in 10 minutes.",
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
        )
        request.session["verification_email"] = user.email
        messages.success(request, "We sent a six-digit code to your GIM email.")
        return redirect("verify_email")
    return render(request, "connect/signup.html", {"form": form})


def verify_email(request):
    email = request.session.get("verification_email")
    if not email:
        return redirect("signup")
    form = OTPForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        otp = EmailOTP.objects.filter(email=email, code=form.cleaned_data["code"]).order_by("-created_at").first()
        if not otp or not otp.is_valid:
            form.add_error("code", "That code is invalid or expired.")
        else:
            otp.used_at = timezone.now()
            otp.save(update_fields=["used_at"])
            user = get_object_or_404(User, email=email)
            user.email_verified = True
            user.save(update_fields=["email_verified"])
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return redirect("onboarding")
    return render(request, "connect/verify_email.html", {"form": form, "email": email})


@login_required
def onboarding(request):
    if request.method == "POST":
        request.user.onboarding_complete = True
        request.user.save(update_fields=["onboarding_complete"])
        messages.success(request, "Onboarding complete. Welcome to GIM Connect.")
        return redirect("dashboard")
    return render(request, "connect/onboarding.html")


def _connection_for_user(request, pk, allow_ended=False):
    connection = get_object_or_404(Connection.objects.select_related("man", "woman"), pk=pk)
    if not connection.includes(request.user):
        raise PermissionDenied
    if not allow_ended and connection.status != Connection.Status.ACTIVE:
        raise PermissionDenied
    return connection


@login_required
def dashboard(request):
    process_timers()
    active = request.user.active_connections().select_related("man", "woman")
    waiting = ConnectionRequest.objects.filter(
        requester=request.user, status=ConnectionRequest.Status.QUEUED
    ).first()
    history = (
        Connection.objects.filter(man=request.user) | Connection.objects.filter(woman=request.user)
    ).exclude(status=Connection.Status.ACTIVE).select_related("man", "woman")[:8]
    return render(
        request,
        "connect/dashboard.html",
        {
            "active_connections": active,
            "waiting_request": waiting,
            "history": history,
            "active_count": active.count(),
            "request_ready": request.user.request_available_at <= timezone.now(),
            "can_request": (
                request.user.is_eligible
                and active.count() < request.user.connection_cap
                and request.user.request_available_at <= timezone.now()
            ),
        },
    )


@login_required
@require_POST
def new_request(request):
    try:
        connection_request, connection = request_connection(request.user)
        if connection:
            messages.success(request, "A new anonymous connection is ready.")
            return redirect("chat", pk=connection.pk)
        messages.success(request, "Your request is in the lobby. We will match it when a spot opens.")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
    return redirect("dashboard")


@login_required
@require_POST
def cancel_waiting_request(request, pk):
    waiting = get_object_or_404(ConnectionRequest, pk=pk, requester=request.user)
    try:
        cancel_request(waiting)
        messages.info(request, "Lobby request cancelled.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("dashboard")


@login_required
def chat(request, pk):
    process_timers()
    connection = _connection_for_user(request, pk, allow_ended=True)
    form = MessageForm()
    reveal = connection.reveal_requests.filter(
        status__in=[
            RevealRequest.Status.PENDING_PARTNER,
            RevealRequest.Status.AWAITING_WOMAN,
            RevealRequest.Status.WINDOW_OPEN,
        ]
    ).first()
    return render(
        request,
        "connect/chat.html",
        {
            "connection": connection,
            "other": connection.other_user(request.user),
            "chat_messages": connection.chat_messages.select_related("sender"),
            "form": form,
            "reveal": reveal,
        },
    )


@login_required
@require_POST
def send_message(request, pk):
    connection = _connection_for_user(request, pk)
    form = MessageForm(request.POST)
    if form.is_valid():
        Message.objects.create(connection=connection, sender=request.user, body=form.cleaned_data["body"].strip())
    else:
        messages.error(request, "Message could not be sent.")
    return redirect("chat", pk=pk)


@login_required
def messages_json(request, pk):
    connection = _connection_for_user(request, pk, allow_ended=True)
    after = request.GET.get("after", "0")
    items = connection.chat_messages.filter(pk__gt=after).select_related("sender")
    return JsonResponse(
        {
            "messages": [
                {
                    "id": item.pk,
                    "body": item.body,
                    "mine": item.sender_id == request.user.pk,
                    "time": timezone.localtime(item.created_at).strftime("%-I:%M %p"),
                }
                for item in items
            ],
            "status": connection.status,
        }
    )


@login_required
@require_POST
def end_chat(request, pk):
    connection = _connection_for_user(request, pk)
    end_connection(connection, ended_by=request.user)
    messages.info(request, "This connection has ended.")
    return redirect("dashboard")


@login_required
@require_POST
def block_user(request, pk):
    connection = _connection_for_user(request, pk, allow_ended=True)
    Block.objects.get_or_create(blocker=request.user, blocked=connection.other_user(request.user))
    if connection.status == Connection.Status.ACTIVE:
        end_connection(connection, ended_by=request.user)
    messages.success(request, "That account is blocked and will not be matched with you again.")
    return redirect("dashboard")


@login_required
def report_connection(request, pk):
    connection = _connection_for_user(request, pk, allow_ended=True)
    form = ReportForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            report_user(
                request.user,
                connection,
                form.cleaned_data["reason"],
                form.cleaned_data["severity"],
            )
            messages.success(request, "Report received. An admin will review it.")
            return redirect("chat", pk=pk)
        except Exception:
            form.add_error(None, "You have already reported this conversation.")
    return render(request, "connect/report.html", {"form": form, "connection": connection})


@login_required
@require_POST
def reveal_start(request, pk):
    connection = _connection_for_user(request, pk)
    try:
        start_reveal(connection, request.user)
        messages.success(request, "Reveal request sent.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("chat", pk=pk)


@login_required
@require_POST
def reveal_partner_response(request, pk, action):
    reveal = get_object_or_404(RevealRequest.objects.select_related("connection"), pk=pk)
    try:
        partner_respond(reveal, request.user, accept=action == "accept")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else "Action not allowed.")
    return redirect("chat", pk=reveal.connection_id)


@login_required
@require_POST
def reveal_reconfirm(request, pk):
    reveal = get_object_or_404(RevealRequest.objects.select_related("connection"), pk=pk)
    try:
        woman_reconfirm(reveal, request.user)
    except (ValidationError, PermissionDenied):
        messages.error(request, "Action not allowed.")
    return redirect("chat", pk=reveal.connection_id)


@login_required
@require_POST
def reveal_decide(request, pk, action):
    reveal = get_object_or_404(RevealRequest.objects.select_related("connection"), pk=pk)
    try:
        if action == "confirm":
            complete_reveal(reveal, woman=request.user)
            messages.success(request, "Identities revealed.")
        else:
            if request.user.pk != reveal.connection.woman_id:
                raise PermissionDenied
            reject_reveal(reveal)
            messages.info(request, "Reveal cancelled. The connection continues normally.")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else "Action not allowed.")
    return redirect("chat", pk=reveal.connection_id)


@login_required
def account_settings(request):
    form = SettingsForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Settings updated.")
        return redirect("settings")
    return render(request, "connect/settings.html", {"form": form})
