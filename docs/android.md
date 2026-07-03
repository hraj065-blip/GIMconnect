# Android App Build and Release Guide

GIM Connect's Android app is a Capacitor wrapper around the live website:

```text
https://gimconnect.vercel.app/
```

The app does not duplicate the Django frontend. The website remains the single source of truth.

## Project layout

```text
mobile/
  capacitor.config.ts
  package.json
  www/index.html
  android/
    app/src/main/AndroidManifest.xml
    app/google-services.json        # manual Firebase file, not committed
```

## Manual configuration required

### Firebase Cloud Messaging

1. Create a free Firebase project.
2. Add an Android app with package name:

```text
in.gimconnect.app
```

3. Download `google-services.json`.
4. Place it at:

```text
mobile/android/app/google-services.json
```

An example placeholder is committed at:

```text
mobile/android/app/google-services.example.json
```

For GitHub Actions, add the full JSON content as this repository secret:

```text
FIREBASE_GOOGLE_SERVICES_JSON
```

For the Django backend to actually send push notifications, create a Firebase service account and set this Vercel environment variable:

```text
FIREBASE_SERVICE_ACCOUNT_JSON=<full-service-account-json>
```

Optional if the service account JSON does not include the right project id:

```text
FCM_PROJECT_ID=<firebase-project-id>
```

## Local debug APK

Requirements:

- Node.js 22+
- Java 17+
- Android Studio or Android SDK command-line tools

Commands:

```bash
cd mobile
npm ci
npx cap sync android
cd android
./gradlew assembleDebug
```

Output:

```text
mobile/android/app/build/outputs/apk/debug/app-debug.apk
```

## Signed release APK

Create a keystore once:

```bash
keytool -genkeypair \
  -v \
  -storetype JKS \
  -keystore upload-keystore.jks \
  -alias gim-connect \
  -keyalg RSA \
  -keysize 2048 \
  -validity 10000
```

Move it to:

```text
mobile/android/app/upload-keystore.jks
```

Create:

```text
mobile/android/keystore.properties
```

With:

```text
storeFile=app/upload-keystore.jks
storePassword=YOUR_STORE_PASSWORD
keyAlias=gim-connect
keyPassword=YOUR_KEY_PASSWORD
```

Build:

```bash
cd mobile/android
./gradlew assembleRelease
```

Output:

```text
mobile/android/app/build/outputs/apk/release/app-release.apk
```

## GitHub Actions build

The workflow is:

```text
.github/workflows/android-apk.yml
```

It builds a debug APK on every push to `main` and on manual workflow runs.

To enable signed release APK builds, add these repository secrets:

```text
ANDROID_KEYSTORE_BASE64
ANDROID_KEYSTORE_PASSWORD
ANDROID_KEY_ALIAS
ANDROID_KEY_PASSWORD
FIREBASE_GOOGLE_SERVICES_JSON
```

Create `ANDROID_KEYSTORE_BASE64` with:

```bash
base64 -i upload-keystore.jks
```

Artifacts:

```text
gim-connect-debug-apk
gim-connect-release-apk
```

## Replacing the APK for users

Recommended path:

1. Run the GitHub Action.
2. Download `app-release.apk` from the `gim-connect-release-apk` artifact.
3. Rename it:

```text
gim-connect-release.apk
```

4. Attach it to the latest GitHub Release.

The website download page points to:

```text
https://github.com/hraj065-blip/GIMconnect/releases/latest/download/gim-connect-release.apk
```

Alternative path:

1. Put the APK at:

```text
static/downloads/gim-connect-latest.apk
```

2. Force-add it because APK files are ignored:

```bash
git add -f static/downloads/gim-connect-latest.apk
```

3. Deploy the website. The download page will use the local APK automatically.

## Android features included

- Capacitor Android wrapper
- Native splash screen
- Native launcher icon
- Android back button support through the website bridge
- Camera and file-upload permissions
- Persistent WebView session cookies
- External links opened through the Capacitor Browser plugin
- Deep links for `https://gimconnect.vercel.app/*` and `gimconnect://`
- Network/offline banner inside the app
- FCM token registration bridge
- Foreground notification banner handling
- Notification tap routing via payload URLs
- Native Android notification channel for messages and connections
- Backend FCM sending hooks for new messages and new anonymous connections

## Vercel deployment

Deploy as usual after pushing to `main`.

Required Vercel variables:

```text
DJANGO_SECRET_KEY
DJANGO_DEBUG=False
DATABASE_URL
EMAIL_HOST_USER
EMAIL_HOST_PASSWORD
DEFAULT_FROM_EMAIL
GIM_ALLOWED_EMAIL_DOMAINS=gim.ac.in
CLOUDINARY_CLOUD_NAME
CLOUDINARY_API_KEY
CLOUDINARY_API_SECRET
FIREBASE_SERVICE_ACCOUNT_JSON
```

Optional:

```text
CUSTOM_DOMAIN
DJANGO_ALLOWED_HOSTS
FCM_PROJECT_ID
```

Run database migrations after deploying schema changes if your production database is not migrated during build.

## Pre-user checklist

- Add real `google-services.json`.
- Add GitHub Actions signing secrets.
- Build and install a debug APK on one Android phone.
- Confirm login persists after closing/reopening the app.
- Confirm selfie upload opens gallery/camera options.
- Confirm Android back button returns from chat to dashboard.
- Confirm notification permission prompt appears after login.
- Confirm `/api/mobile/push-token/` creates a `PushDevice`.
- Confirm `FIREBASE_SERVICE_ACCOUNT_JSON` is set before expecting real background push notifications.
- Confirm a new message creates a phone notification when the app is backgrounded.
- Confirm a new connection creates a phone notification when the app is backgrounded.
- Publish a signed release APK and attach it to the latest GitHub Release.
- Test the website `/android/` download page on phone.
