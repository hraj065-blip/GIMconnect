"""
GIM Connect – views.py
======================
All HTTP view logic for the connect app.
"""

import json
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
from django.templatetags.static import static
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
    PushDevice,
    User,
)
from .services import (
    cancel_request,
    complete_reveal,
    end_connection,
    partner_respond,
    process_timers,
    reject_reveal,
    cancel_reveal,
    report_user,
    request_connection,
    start_reveal,
    woman_reconfirm,
)
from .notifications import notify_new_message

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


def _get_connection_for_user(request, pk, *, allow_ended=False, select_related=True):
    qs = Connection.objects
    if select_related:
        qs = qs.select_related("man", "woman")
    connection = get_object_or_404(qs, pk=pk)
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


def _json_body(request):
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


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


def android_download(request):
    apk_static_path = "downloads/gim-connect-latest.apk"
    apk_file = settings.BASE_DIR / "static" / apk_static_path
    has_local_apk = apk_file.exists()
    apk_url = (
        static(apk_static_path)
        if has_local_apk
        else "https://github.com/hraj065-blip/GIMconnect/releases/latest/download/gim-connect-release.apk"
    )

    return render(
        request,
        "connect/android_download.html",
        {
            "apk_url": apk_url,
            "has_local_apk": has_local_apk,
        },
    )


def signup(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    # REMOVED request.FILES since the photo is now uploaded on the dashboard
    form = SignupForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                user = form.save(commit=False)
                user.email = user.email.lower().strip()
                # Set photo_status to PENDING automatically so the dashboard prompts them
                user.photo_status = User.PhotoStatus.PENDING 
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

    # ── NEW: Catch photo uploads directly from the dashboard ──
    if request.method == "POST" and "photo" in request.FILES:
        request.user.photo = request.FILES["photo"]
        request.user.photo_status = User.PhotoStatus.PENDING
        request.user.save(update_fields=["photo", "photo_status"])
        messages.success(request, "Your selfie has been uploaded and is pending review!")
        return redirect("dashboard")

    active_base = request.user.active_connections()
    active_count = active_base.count()

    active_qs = (
        active_base
        .select_related("man", "woman")
    )

    # Attach a human-readable countdown string to each connection so the
    # template can display "3 days left" / "14 hours left" without extra queries.
    now = timezone.now()
    active = []
    for conn in active_qs:
        other = conn.other_user(request.user)
        conn.current_other_user = other
        conn.anonymous_display_name = other.anonymous_display_name
        conn.anonymous_intro = other.anonymous_intro.strip()
        delta = conn.expires_at - now
        total_seconds = max(int(delta.total_seconds()), 0)
        total_hours = total_seconds // 3600
        if total_hours >= 48:
            conn.expires_in_label = f"{total_hours // 24} days left"
        elif total_hours >= 24:
            conn.expires_in_label = "1 day left"
        elif total_hours >= 2:
            conn.expires_in_label = f"{total_hours} hours left"
        elif total_hours == 1:
            conn.expires_in_label = "1 hour left"
        else:
            remaining_mins = total_seconds // 60
            conn.expires_in_label = f"{remaining_mins} min left" if remaining_mins > 0 else "Expiring soon"
        conn.expires_in_urgent = total_hours < 24
        last_message = (
            conn.chat_messages
            .only("body")
            .order_by("-created_at", "-pk")
            .first()
        )
        conn.last_message_body = last_message.get_body if last_message else ""
        active.append(conn)

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
    ).order_by("-ended_at")[:8]

    request_ready = request.user.request_available_at <= now

    can_request = (
        request.user.is_eligible
        and request.user.is_available
        and active_count < request.user.connection_cap
        and request.user.request_available_at <= now
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
        .first()
    )
    if reveal is not None:
        # `connection` is already loaded here (with man/woman select_related).
        # Reuse it so reveal.connection / reveal.connection.man/.woman in the
        # template don't trigger extra queries.
        reveal.connection = connection
    other = connection.other_user(request.user)

    return render(
        request,
        "connect/chat.html",
        {
            "connection": connection,
            "other": other,
            "anonymous_display_name": other.anonymous_display_name,
            "anonymous_intro": other.anonymous_intro.strip(),
            "chat_messages": (
                connection.chat_messages
                .select_related("sender", "reply_to", "reply_to__sender")
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
    connection = _get_connection_for_user(request, pk, select_related=False)
    form = MessageForm(request.POST)
    if form.is_valid():
        body = form.cleaned_data["body"].strip()
        if body:
            reply_to = None
            reply_to_id = form.cleaned_data.get("reply_to")
            if reply_to_id:
                reply_to = Message.objects.filter(
                    pk=reply_to_id,
                    connection=connection,
                ).first()
            message = Message.objects.create(
                connection=connection,
                sender=request.user,
                reply_to=reply_to,
                body=body,
            )
            transaction.on_commit(lambda: notify_new_message(message.pk))
    else:
        messages.error(request, "Message could not be sent.")
    return redirect("chat", pk=pk)


@login_required
@_rate_limited("gim:poll", limit_seconds=2)
def messages_json(request, pk):
    connection = _get_connection_for_user(request, pk, allow_ended=True, select_related=False)

    try:
        after = int(request.GET.get("after", "0"))
    except (ValueError, TypeError):
        after = 0

    items = (
        connection.chat_messages
        .filter(pk__gt=after)
        .select_related("reply_to", "reply_to__sender")
        .only("id", "body", "sender", "created_at", "reply_to__id", "reply_to__body", "reply_to__sender")
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
                    "body": item.get_body,
                    "mine": item.sender_id == request.user.pk,
                    "time": timezone.localtime(item.created_at).strftime("%-I:%M %p"),
                    "reply": (
                        {
                            "id": item.reply_to_id,
                            "body": item.reply_to.get_body,
                            "mine": item.reply_to.sender_id == request.user.pk,
                        }
                        if item.reply_to_id
                        else None
                    ),
                }
                for item in items
            ],
            "status": connection.status,
            "reveal_status": active_reveal,
            "identities_revealed": connection.identities_revealed,
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
def reveal_abort(request, pk):
    """Cancel a reveal request that has not yet reached the photo window.
    Valid when status is PENDING_PARTNER or AWAITING_WOMAN."""
    reveal = get_object_or_404(
        RevealRequest.objects.select_related("connection"), pk=pk
    )
    try:
        if not reveal.connection.includes(request.user):
            raise PermissionDenied
        if reveal.status not in (
            RevealRequest.Status.PENDING_PARTNER,
            RevealRequest.Status.AWAITING_WOMAN,
        ):
            raise ValidationError("Cannot abort at this stage.")
        reject_reveal(reveal)
        messages.info(request, "Reveal cancelled. The connection continues.")
    except (ValidationError, PermissionDenied):
        pass
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
            cancel_reveal(reveal, woman=request.user)
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
@require_POST
def resend_verification(request):
    user = request.user
    
    if user.email_verified:
        messages.info(request, "Your email is already verified.")
        return redirect("dashboard")

    try:
        otp = EmailOTP.issue(user.email)

        send_mail(
            subject="Your new GIM Connect verification code",
            message=(
                f"Your new verification code is {otp.code}.\n"
                "It expires in 10 minutes.\n\n"
                "If you did not request this, please ignore this email."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )

        request.session["verification_email"] = user.email
        messages.success(request, "A new six-digit code has been sent to your GIM email.")
        return redirect("verify_email")

    except Exception:
        logger.exception("resend_verification: failed to send new OTP for %s", user.email)
        messages.error(
            request, 
            "We could not send a new verification email right now. Please try again later."
        )
        return redirect("dashboard")


@login_required
def account_settings(request):
    form = SettingsForm(request.POST or None, request.FILES or None, instance=request.user)

    if request.method == "POST" and form.is_valid():
        user = form.save(commit=False)

        if "photo" in form.changed_data:
            user.photo_status = User.PhotoStatus.PENDING
            # Photo is optional for both genders and never blocks eligibility.
            messages.info(
                request,
                "Your photo has been submitted for review. "
                "It will appear during identity reveals once approved. "
                "Your connections are not affected.",
            )

        user.save()
        update_session_auth_hash(request, user)
        messages.success(request, "Settings updated successfully.")
        return redirect("settings")

    return render(request, "connect/settings.html", {"form": form})


@login_required
@require_POST
def toggle_availability(request):
    """Flip the user's is_available flag.
    When pausing, any pending lobby request is cancelled automatically so the
    user doesn't get matched while they've asked not to be."""
    user = request.user
    going_available = not user.is_available     # the NEW state after the toggle
    user.is_available = going_available
    user.save(update_fields=["is_available"])
 
    if not going_available:
        # Cancel a pending lobby request if one exists — no point staying in
        # the queue while matching is paused.
        pending = ConnectionRequest.objects.filter(
            requester=user,
            status=ConnectionRequest.Status.QUEUED,
        ).first()
        if pending:
            try:
                cancel_request(pending)
            except ValidationError:
                pass
        messages.info(request, "Matching paused. Your active conversations continue normally.")
    else:
        messages.success(request, "You're back in the pool. New connections can start again.")
 
    return redirect("dashboard")


# ---------------------------------------------------------------------------
# Mobile app API
# ---------------------------------------------------------------------------

@login_required
@require_POST
def register_push_token(request):
    payload = _json_body(request)
    token = str(payload.get("token", "")).strip()

    if not token:
        return _json_error("Missing push token.")

    device, _created = PushDevice.objects.update_or_create(
        token=token,
        defaults={
            "user": request.user,
            "platform": PushDevice.Platform.ANDROID,
            "device_id": str(payload.get("deviceId", "")).strip()[:128],
            "app_version": str(payload.get("appVersion", "")).strip()[:40],
            "is_active": True,
            "last_seen_at": timezone.now(),
        },
    )

    return JsonResponse({"ok": True, "deviceId": device.pk})


@login_required
@require_POST
def unregister_push_token(request):
    payload = _json_body(request)
    token = str(payload.get("token", "")).strip()

    if not token:
        return _json_error("Missing push token.")

    PushDevice.objects.filter(user=request.user, token=token).update(
        is_active=False,
        last_seen_at=timezone.now(),
    )
    return JsonResponse({"ok": True})
