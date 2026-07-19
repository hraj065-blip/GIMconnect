from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy
from django.contrib.staticfiles.storage import staticfiles_storage
from django.views.generic.base import RedirectView

from .forms import LoginForm
from . import views

urlpatterns = [
    path("", views.landing, name="landing"),
    path("android/", views.android_download, name="android_download"),
    path("signup/", views.signup, name="signup"),
    path("verify/", views.verify_email, name="verify_email"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="registration/login.html", authentication_form=LoginForm),
        name="login",
    ),
    path(
        "password-reset/",
        auth_views.PasswordResetView.as_view(
            template_name="registration/password_reset_form.html",
            email_template_name="registration/password_reset_email.html",
            subject_template_name="registration/password_reset_subject.txt",
            success_url=reverse_lazy("password_reset_done"),
        ),
        name="password_reset",
    ),
    path(
        "password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(template_name="registration/password_reset_done.html"),
        name="password_reset_done",
    ),
    path(
        "reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="registration/password_reset_confirm.html",
            success_url=reverse_lazy("password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path(
        "reset/done/",
        auth_views.PasswordResetCompleteView.as_view(template_name="registration/password_reset_complete.html"),
        name="password_reset_complete",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("onboarding/", views.onboarding, name="onboarding"),
    path("app/availability/toggle/", views.toggle_availability, name="toggle_availability"),
    path("app/", views.dashboard, name="dashboard"),
    path("app/request/", views.new_request, name="new_request"),
    path("app/request/<int:pk>/cancel/", views.cancel_waiting_request, name="cancel_request"),
    path("app/chat/<int:pk>/", views.chat, name="chat"),
    path("app/chat/<int:pk>/send/", views.send_message, name="send_message"),
    path("app/chat/<int:pk>/messages/", views.messages_json, name="messages_json"),
    path("app/chat/<int:pk>/end/", views.end_chat, name="end_chat"),
    path("app/chat/<int:pk>/block/", views.block_user, name="block_user"),
    path("app/chat/<int:pk>/report/", views.report_connection, name="report"),
    path("app/chat/<int:pk>/reveal/", views.reveal_start, name="reveal_start"),
    path("app/reveal/<int:pk>/respond/<str:action>/", views.reveal_partner_response, name="reveal_response"),
    path("app/reveal/<int:pk>/reconfirm/", views.reveal_reconfirm, name="reveal_reconfirm"),
    path("app/reveal/<int:pk>/decide/<str:action>/", views.reveal_decide, name="reveal_decide"),
    path("app/settings/", views.account_settings, name="settings"),
    path("verify/resend/", views.resend_verification, name="resend_verification"),
    path('chat/reveal/<int:pk>/abort/', views.reveal_abort, name='reveal_abort'),
    path("api/mobile/push-token/", views.register_push_token, name="register_push_token"),
    path("api/mobile/push-token/remove/", views.unregister_push_token, name="unregister_push_token"),
]
