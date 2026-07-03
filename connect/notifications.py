import json
import logging

import requests
from django.conf import settings
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account

from .models import Connection, Message, PushDevice

logger = logging.getLogger(__name__)

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
FCM_CHANNEL_ID = "gim_connect_messages"


def _firebase_context():
    raw = getattr(settings, "FIREBASE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        return None

    try:
        info = json.loads(raw)
        project_id = getattr(settings, "FCM_PROJECT_ID", "") or info.get("project_id")
        if not project_id:
            logger.warning("FCM project id is missing.")
            return None

        credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=[FCM_SCOPE],
        )
        credentials.refresh(GoogleAuthRequest())
        return project_id, credentials.token
    except Exception:
        logger.exception("Could not initialise Firebase Cloud Messaging.")
        return None


def _mark_token_inactive(token):
    PushDevice.objects.filter(token=token).update(is_active=False)


def _send_to_token(token, title, body, url):
    context = _firebase_context()
    if context is None:
        return False

    project_id, access_token = context
    endpoint = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    payload = {
        "message": {
            "token": token,
            "notification": {
                "title": title,
                "body": body,
            },
            "data": {
                "url": url,
                "path": url,
            },
            "android": {
                "priority": "HIGH",
                "notification": {
                    "channel_id": FCM_CHANNEL_ID,
                    "sound": "default",
                },
            },
        }
    }

    try:
        response = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=6,
        )
    except requests.RequestException:
        logger.exception("FCM request failed.")
        return False

    if response.status_code == 200:
        return True

    body_text = response.text.upper()
    if "UNREGISTERED" in body_text or "INVALID_ARGUMENT" in body_text:
        _mark_token_inactive(token)
    logger.warning("FCM send failed with status %s: %s", response.status_code, response.text[:400])
    return False


def send_push_to_user(user, title, body, url):
    tokens = list(
        PushDevice.objects.filter(user=user, is_active=True)
        .values_list("token", flat=True)
    )
    sent = 0
    for token in tokens:
        if _send_to_token(token, title, body, url):
            sent += 1
    return sent


def notify_new_connection(connection_id):
    try:
        connection = Connection.objects.select_related("man", "woman").get(pk=connection_id)
    except Connection.DoesNotExist:
        return

    url = f"/app/chat/{connection.pk}/"
    for user in (connection.man, connection.woman):
        send_push_to_user(
            user,
            "New anonymous connection",
            "A private conversation is ready on GIM Connect.",
            url,
        )


def notify_new_message(message_id):
    try:
        message = (
            Message.objects
            .select_related("connection__man", "connection__woman", "sender")
            .get(pk=message_id)
        )
    except Message.DoesNotExist:
        return

    recipient = message.connection.other_user(message.sender)
    send_push_to_user(
        recipient,
        "New anonymous message",
        "You have a new message on GIM Connect.",
        f"/app/chat/{message.connection_id}/",
    )
