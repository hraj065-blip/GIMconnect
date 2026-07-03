import type { CapacitorConfig } from "@capacitor/cli";

const config: CapacitorConfig = {
  appId: "in.gimconnect.app",
  appName: "GIM Connect",
  webDir: "www",
  server: {
    url: "https://gimconnect.vercel.app",
    cleartext: false,
    allowNavigation: ["gimconnect.vercel.app"],
    errorPath: "index.html"
  },
  android: {
    allowMixedContent: false,
    captureInput: true,
    webContentsDebuggingEnabled: false
  },
  plugins: {
    SplashScreen: {
      launchShowDuration: 1200,
      launchAutoHide: true,
      backgroundColor: "#0f2f24",
      androidSplashResourceName: "gim_splash",
      androidScaleType: "CENTER_CROP",
      showSpinner: false
    },
    PushNotifications: {
      presentationOptions: ["badge", "sound", "alert"]
    },
    StatusBar: {
      overlaysWebView: false,
      style: "LIGHT",
      backgroundColor: "#0f2f24"
    }
  }
};

export default config;
