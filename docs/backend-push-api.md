# Mobile Push Backend API

The Android app registers Firebase Cloud Messaging tokens with the Django backend.

## Register token

```http
POST /api/mobile/push-token/
Content-Type: application/json
X-CSRFToken: <csrftoken>
```

Body:

```json
{
  "token": "firebase-device-token",
  "platform": "android",
  "deviceId": "android-local-device-id",
  "appVersion": "1.0.0"
}
```

Response:

```json
{
  "ok": true,
  "deviceId": 1
}
```

## Unregister token

```http
POST /api/mobile/push-token/remove/
Content-Type: application/json
X-CSRFToken: <csrftoken>
```

Body:

```json
{
  "token": "firebase-device-token"
}
```

Response:

```json
{
  "ok": true
}
```

## Notification payload format

When the backend later sends FCM notifications, include a route in `data`.

Example for a chat:

```json
{
  "notification": {
    "title": "New anonymous message",
    "body": "You have a new message on GIM Connect."
  },
  "data": {
    "url": "/app/chat/123/"
  }
}
```

The Android bridge also understands:

```text
data.path
data.chat_url
```

## Sending notifications

The backend now sends FCM notifications for:

- New anonymous connections
- New chat messages

Set this environment variable in Vercel:

```text
FIREBASE_SERVICE_ACCOUNT_JSON=<full-service-account-json>
```

Optional:

```text
FCM_PROJECT_ID=<firebase-project-id>
```

Do not commit Firebase service-account private keys.
