(function () {
  "use strict";

  const capacitor = window.Capacitor;
  const plugins = capacitor && capacitor.Plugins ? capacitor.Plugins : {};
  const isNative = Boolean(capacitor && typeof capacitor.isNativePlatform === "function" && capacitor.isNativePlatform());

  if (!isNative) return;

  const root = document.documentElement;
  root.classList.add("native-app", "native-insets-managed");

  const App = plugins.App;
  const Browser = plugins.Browser;
  const Network = plugins.Network;
  const PushNotifications = plugins.PushNotifications;
  const StatusBar = plugins.StatusBar;

  const PUSH_PROMPT_KEY = "gim_connect_push_prompt_seen";
  let pushListenersBound = false;

  function getCookie(name) {
    return document.cookie
      .split(";")
      .map((part) => part.trim())
      .filter(Boolean)
      .reduce((value, part) => {
        const index = part.indexOf("=");
        const key = index >= 0 ? part.slice(0, index) : part;
        if (key !== name) return value;
        return decodeURIComponent(index >= 0 ? part.slice(index + 1) : "");
      }, "");
  }

  function getDeviceId() {
    const key = "gim_connect_device_id";
    let value = window.localStorage.getItem(key);
    if (!value) {
      value = "android-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
      window.localStorage.setItem(key, value);
    }
    return value;
  }

  function navigateToAppUrl(rawUrl) {
    if (!rawUrl) return;

    try {
      const parsed = new URL(rawUrl);
      if (parsed.protocol === "gimconnect:") {
        window.location.href = parsed.pathname || "/app/";
        return;
      }
      if (parsed.hostname === "gimconnect.vercel.app" || parsed.hostname === window.location.hostname) {
        window.location.href = parsed.pathname + parsed.search + parsed.hash;
      }
    } catch (_error) {
      if (rawUrl.charAt(0) === "/") window.location.href = rawUrl;
    }
  }

  async function configureNativeChrome() {
    if (!StatusBar) return;

    try {
      if (typeof StatusBar.setOverlaysWebView === "function") {
        await StatusBar.setOverlaysWebView({ overlay: false });
      }
      if (typeof StatusBar.setBackgroundColor === "function") {
        await StatusBar.setBackgroundColor({ color: "#F6F1E8" });
      }
      if (typeof StatusBar.setStyle === "function") {
        await StatusBar.setStyle({ style: "LIGHT" });
      }
    } catch (_error) {}
  }

  async function postPushToken(token) {
    if (!token || document.body.dataset.authenticated !== "true") return;

    try {
      await fetch("/api/mobile/push-token/", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCookie("csrftoken")
        },
        body: JSON.stringify({
          token: token,
          platform: "android",
          deviceId: getDeviceId(),
          appVersion: "1.0.0"
        })
      });
    } catch (_error) {
      // Token registration can retry on the next app open.
    }
  }

  function ensureNetworkBanner() {
    let banner = document.getElementById("mobile-network-banner");
    if (banner) return banner;

    banner = document.createElement("div");
    banner.id = "mobile-network-banner";
    banner.className = "mobile-network-banner";
    banner.textContent = "You are offline. GIM Connect will reconnect automatically.";
    banner.hidden = true;
    document.body.appendChild(banner);
    return banner;
  }

  function setOnlineStatus(status) {
    const online = Boolean(status && status.connected);
    root.classList.toggle("is-offline", !online);
    ensureNetworkBanner().hidden = online;
  }

  function showForegroundNotification(notification) {
    const title = notification && notification.title ? notification.title : "GIM Connect";
    const body = notification && notification.body ? notification.body : "You have a new update.";
    const data = notification && notification.data ? notification.data : {};

    const banner = document.createElement("button");
    banner.type = "button";
    banner.className = "mobile-push-banner";
    banner.innerHTML = "<strong></strong><span></span>";
    banner.querySelector("strong").textContent = title;
    banner.querySelector("span").textContent = body;
    banner.addEventListener("click", function () {
      banner.remove();
      navigateToAppUrl(data.url || data.path || data.chat_url || "");
    });

    document.body.appendChild(banner);
    window.setTimeout(function () {
      banner.remove();
    }, 6000);
  }

  function ensureNotificationPrompt(message) {
    if (document.body.dataset.authenticated !== "true" || !PushNotifications) return null;

    let card = document.getElementById("mobile-notification-card");
    if (card) {
      if (message) card.querySelector("p").textContent = message;
      return card;
    }

    card = document.createElement("section");
    card.id = "mobile-notification-card";
    card.className = "mobile-notification-card";
    card.innerHTML = [
      "<div>",
      "<strong>Turn on connection alerts</strong>",
      "<p>Get notified for new messages and new anonymous connections.</p>",
      "</div>",
      "<button type=\"button\">Enable</button>"
    ].join("");

    card.querySelector("button").addEventListener("click", function () {
      requestPushNotifications(true);
    });

    document.body.appendChild(card);
    return card;
  }

  function hideNotificationPrompt() {
    const card = document.getElementById("mobile-notification-card");
    if (card) card.remove();
  }

  async function getPushPermission() {
    if (!PushNotifications || typeof PushNotifications.checkPermissions !== "function") {
      return { receive: "denied" };
    }
    try {
      return await PushNotifications.checkPermissions();
    } catch (_error) {
      return { receive: "denied" };
    }
  }

  async function requestPushNotifications(force) {
    if (!PushNotifications || document.body.dataset.authenticated !== "true") return;

    try {
      window.localStorage.setItem(PUSH_PROMPT_KEY, "1");
      const permission = typeof PushNotifications.requestPermissions === "function"
        ? await PushNotifications.requestPermissions()
        : { receive: "denied" };

      if (permission.receive === "granted") {
        hideNotificationPrompt();
        await PushNotifications.register();
        return;
      }

      ensureNotificationPrompt("Notifications are off. Tap Enable and allow notifications in Android settings.");
    } catch (_error) {
      if (force) {
        ensureNotificationPrompt("Could not open the Android permission prompt. Check app notification settings.");
      }
    }
  }

  async function setupPushNotifications() {
    if (!PushNotifications || document.body.dataset.authenticated !== "true") return;

    if (!pushListenersBound) {
      pushListenersBound = true;
      PushNotifications.addListener("registration", function (token) {
        postPushToken(token && token.value);
      });
      PushNotifications.addListener("registrationError", function () {
        ensureNotificationPrompt("Notification registration failed. Check Firebase setup and try again.");
      });
      PushNotifications.addListener("pushNotificationReceived", showForegroundNotification);
      PushNotifications.addListener("pushNotificationActionPerformed", function (event) {
        const data = event && event.notification ? event.notification.data : {};
        navigateToAppUrl(data.url || data.path || data.chat_url || "");
      });
    }

    const permission = await getPushPermission();
    if (permission.receive === "granted") {
      hideNotificationPrompt();
      try {
        await PushNotifications.register();
      } catch (_error) {}
      return;
    }

    ensureNotificationPrompt();
    if (window.localStorage.getItem(PUSH_PROMPT_KEY) !== "1") {
      window.setTimeout(function () {
        requestPushNotifications(false);
      }, 900);
    }
  }

  function isExternalUrl(url) {
    return url.protocol === "http:" || url.protocol === "https:"
      ? !["gimconnect.vercel.app", window.location.hostname].includes(url.hostname)
      : !["", "mailto:", "tel:"].includes(url.protocol);
  }

  document.addEventListener("click", function (event) {
    const link = event.target.closest ? event.target.closest("a[href]") : null;
    if (!link || !Browser) return;

    const href = link.getAttribute("href");
    if (!href || href.charAt(0) === "#") return;

    let url;
    try {
      url = new URL(href, window.location.href);
    } catch (_error) {
      return;
    }

    if (!isExternalUrl(url)) return;

    event.preventDefault();
    Browser.open({ url: url.href });
  });

  document.addEventListener("DOMContentLoaded", async function () {
    await configureNativeChrome();

    if (Network) {
      try {
        setOnlineStatus(await Network.getStatus());
        Network.addListener("networkStatusChange", setOnlineStatus);
      } catch (_error) {}
    }

    if (App) {
      App.addListener("backButton", function (event) {
        if (window.location.pathname.startsWith("/app/chat/")) {
          window.location.href = "/app/";
          return;
        }
        if (event && event.canGoBack) {
          window.history.back();
          return;
        }
        if (typeof App.exitApp === "function") App.exitApp();
      });

      App.addListener("appUrlOpen", function (event) {
        navigateToAppUrl(event && event.url);
      });
    }

    await setupPushNotifications();
  });
})();
