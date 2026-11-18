BUILD OUTPUT
============
The Android (.apk) and Windows (.exe) installers land in this folder.

  Android :  cd cordova && npm install && ./build-android.sh
  Windows :  cd electron && npm install && npm run build

Both scripts write their artifacts here, so this folder stays empty until you
actually run a build. Nothing here is needed to use the app in a browser.
