from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Block, Connection, ConnectionRequest, EmailOTP, Message, Report, RevealRequest, User


@admin.action(description="Approve selected verification photos")
def approve_photos(modeladmin, request, queryset):
    queryset.update(photo_status=User.PhotoStatus.APPROVED)


@admin.register(User)
class ConnectUserAdmin(UserAdmin):
    ordering = ("email",)
    list_display = ("email", "display_name", "gender", "email_verified", "photo_status", "is_blocked")
    list_filter = ("gender", "email_verified", "photo_status", "is_blocked")
    actions = (approve_photos,)
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("display_name", "gender", "photo")}),
        ("Verification", {"fields": ("email_verified", "photo_status", "onboarding_complete")}),
        ("Safety", {"fields": ("suspended_until", "is_blocked")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined", "request_available_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "display_name", "gender", "password1", "password2"),
            },
        ),
    )
    search_fields = ("email", "display_name")


@admin.register(Connection)
class ConnectionAdmin(admin.ModelAdmin):
    list_display = ("id", "man", "woman", "status", "established_at", "identities_revealed")
    list_filter = ("status", "identities_revealed")


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ("reporter", "reported_user", "severity", "status", "created_at")
    list_filter = ("severity", "status")


admin.site.register(ConnectionRequest)
admin.site.register(EmailOTP)
admin.site.register(Message)
admin.site.register(RevealRequest)
admin.site.register(Block)
