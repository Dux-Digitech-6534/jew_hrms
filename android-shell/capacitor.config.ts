import type { CapacitorConfig } from "@capacitor/cli";

const config: CapacitorConfig = {
  appId: "com.duxdigitech.jewhrms",
  appName: "JEW HRMS",
  webDir: "capacitor-www",
  server: {
    url: "https://jewipl.duxdigitech.in/jew-hrms/m",
    cleartext: false
  },
  plugins: {
    SplashScreen: {
      launchShowDuration: 1200,
      backgroundColor: "#ffffff"
    }
  }
};

export default config;
