from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import MessageForm, OTPForm, ReportForm, SettingsForm, SignupForm
from .models import Block, Connection, ConnectionRequest, EmailOTP, Message, RevealRequest, User
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


def landing(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(request, "connect/landing.html")


def signup(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = SignupForm(request.POST or None, request.FILES or None)
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
