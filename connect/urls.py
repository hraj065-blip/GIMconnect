from django.contrib.auth import views as auth_views
from django.urls import path
from django.contrib.staticfiles.storage import staticfiles_storage
from django.views.generic.base import RedirectView

from .forms import LoginForm
from . import views

urlpatterns = [
    path('favicon.ico', RedirectView.as_view(url='/favicon.ico', permanent=True)),
    path("", views.landing, name="landing"),
    path("signup/", views.signup, name="signup"),
    path("verify/", views.verify_email, name="verify_email"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="registration/login.html", authentication_form=LoginForm),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("onboarding/", views.onboarding, name="onboarding"),
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
]
