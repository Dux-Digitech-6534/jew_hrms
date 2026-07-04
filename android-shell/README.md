# JEW HRMS Android Shell

Thin Capacitor shell for Jain Engineering Works HRMS.

- App ID: `com.duxdigitech.jewhrms`
- App name: `JEW HRMS`
- Hosted URL: `https://jewipl.duxdigitech.in/jew-hrms/m`
- The frontend is not bundled for production updates; Capacitor loads `server.url`.

## Debug APK

```bash
npm install
npx cap add android
npx cap sync android
cd android
./gradlew assembleDebug
```

Release signing requires a private keystore and should not be committed.
