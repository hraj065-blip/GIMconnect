import secrets
from datetime import timedelta

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.validators import MinLengthValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("email_verified", True)
        extra_fields.setdefault("photo_status", "approved")
        extra_fields.setdefault("onboarding_complete", True)
        if extra_fields.get("is_staff") is not True or extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_staff=True and is_superuser=True")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    class Gender(models.TextChoices):
        MAN = "M", "Man"
        WOMAN = "F", "Woman"

    class PhotoStatus(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    username = None
    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=80)
    gender = models.CharField(max_length=1, choices=Gender.choices)
    photo = models.ImageField(upload_to="verification_photos/%Y/%m/", blank=True)
    email_verified = models.BooleanField(default=False)
    photo_status = models.CharField(
        max_length=12, choices=PhotoStatus.choices, default=PhotoStatus.PENDING
    )
    onboarding_complete = models.BooleanField(default=False)
    request_available_at = models.DateTimeField(default=timezone.now)
    suspended_until = models.DateTimeField(null=True, blank=True)
    is_blocked = models.BooleanField(default=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["display_name", "gender"]
    objects = UserManager()

    def __str__(self):
        return self.email

    @property
    def is_suspended(self):
        return bool(self.suspended_until and self.suspended_until > timezone.now())

    @property
    def is_eligible(self):
        return (
            self.email_verified
            and self.photo_status == self.PhotoStatus.APPROVED
            and self.onboarding_complete
            and not self.is_blocked
            and not self.is_suspended
        )

    @property
    def connection_cap(self):
        return 2 if self.gender == self.Gender.MAN else 5

    def active_connections(self):
        lookup = Q(man=self) if self.gender == self.Gender.MAN else Q(woman=self)
        return Connection.objects.filter(lookup, status=Connection.Status.ACTIVE)


class EmailOTP(models.Model):
    email = models.EmailField(db_index=True)
    code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["email", "code"]),
        ]

    def __str__(self):
        return f"OTP for {self.email}"

    @classmethod
    def issue(cls, email):
        return cls.objects.create(
            email=email.lower(),
            code=f"{secrets.randbelow(1_000_000):06d}",
            expires_at=timezone.now() + timedelta(minutes=10),
        )

    @property
    def is_valid(self):
        return not self.used_at and self.expires_at > timezone.now()


class ConnectionRequest(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Waiting in lobby"
        MATCHED = "matched", "Matched"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired and refunded"

    requester = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="connection_requests"
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.QUEUED
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    matched_connection = models.OneToOneField(
        "Connection",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_request",
    )

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["requester", "status"]),
        ]

    def __str__(self):
        return f"Request({self.requester} — {self.get_status_display()})"


class Connection(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ENDED = "ended", "Ended"
        EXPIRED = "expired", "Expired"

    man = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="connections_as_man"
    )
    woman = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="connections_as_woman"
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE
    )
    established_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ended_connections",
    )
    identities_revealed = models.BooleanField(default=False)
    revealed_at = models.DateTimeField(null=True, blank=True)
    reveal_cooldown_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-established_at"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(man=models.F("woman")),
                name="connection_users_must_differ",
            )
        ]
        indexes = [
            models.Index(fields=["man", "status"]),
            models.Index(fields=["woman", "status"]),
        ]

    def __str__(self):
        return f"Connection #{self.pk}: {self.man} / {self.woman} [{self.status}]"

    def other_user(self, user):
        return self.woman if user.pk == self.man_id else self.man

    def includes(self, user):
        return user.pk in {self.man_id, self.woman_id}


class Message(models.Model):
    connection = models.ForeignKey(
        Connection, on_delete=models.CASCADE, related_name="chat_messages"
    )
    sender = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="sent_messages"
    )
    body = models.TextField(validators=[MinLengthValidator(1)], max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["connection", "created_at"]),
            models.Index(fields=["connection", "id"]), # Highly optimized for delta polling logic
        ]

    def __str__(self):
        return f"Msg #{self.pk} in Connection #{self.connection_id} by {self.sender}"


class RevealRequest(models.Model):
    class Status(models.TextChoices):
        PENDING_PARTNER = "pending_partner", "Waiting for partner"
        AWAITING_WOMAN = "awaiting_woman", "Waiting for woman to reconfirm"
        WINDOW_OPEN = "window_open", "Photo review window open"
        REJECTED = "rejected", "Rejected by partner"
        CANCELLED = "cancelled", "Cancelled during photo window"
        REVEALED = "revealed", "Revealed"

    connection = models.ForeignKey(
        Connection, on_delete=models.CASCADE, related_name="reveal_requests"
    )
    requester = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="reveal_requests"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING_PARTNER
    )
    created_at = models.DateTimeField(auto_now_add=True)
    partner_responded_at = models.DateTimeField(null=True, blank=True)
    window_opened_at = models.DateTimeField(null=True, blank=True)
    window_expires_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["connection", "status"]),
        ]

    def __str__(self):
        return f"Reveal #{self.pk} on Connection #{self.connection_id} [{self.status}]"


class Block(models.Model):
    blocker = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="blocks_made"
    )
    blocked = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="blocks_received"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["blocker", "blocked"], name="unique_block_pair"
            ),
        ]

    def __str__(self):
        return f"{self.blocker} blocked {self.blocked}"


class Report(models.Model):
    class Severity(models.TextChoices):
        STANDARD = "standard", "Standard"
        SEVERE = "severe", "Severe / immediate safety concern"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        REVIEWED = "reviewed", "Reviewed"
        DISMISSED = "dismissed", "Dismissed"

    reporter = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="reports_made"
    )
    reported_user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="reports_received"
    )
    connection = models.ForeignKey(
        Connection, on_delete=models.SET_NULL, null=True, related_name="reports"
    )
    reason = models.TextField(max_length=2000)
    severity = models.CharField(
        max_length=10, choices=Severity.choices, default=Severity.STANDARD
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.OPEN
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["reporter", "connection"], name="one_report_per_connection"
            ),
        ]

    def __str__(self):
        return f"Report #{self.pk}: {self.reporter} → {self.reported_user} [{self.severity}]"
