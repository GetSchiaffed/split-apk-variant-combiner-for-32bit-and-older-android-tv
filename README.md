# Split APK variant combiner for 32-bit and older Android TVs

Install the **current** version of an app on an Android TV that the Play Store calls "not compatible" because the TV
is 32-bit, or on an older Android, **without modifying, re-signing or repacking a single file**.

It works when a developer publishes one version in several variants, each of which fails on your TV for a different
reason, but whose pieces fit together. A real case, an Android TV app on a 32-bit Android 11 TV:

| Variant | Its base APK needs | Its native code is for | On a 32-bit Android 11 TV |
|---|---|---|---|
| arm64-v8a | Android 10+ | 64-bit only | installs, crashes at start: the 32-bit library is missing |
| armeabi-v7a | Android 12L+ | 32-bit | refused: needs a newer Android |
| **base of the first + native split of the second** | **Android 10+** | **32-bit** | **installs and runs** |

Both variants are the same package, the same version and the same signature, so Android treats their pieces as one
app. Every piece stays the developer's original, signed file.

## How it works

Apps from the Play Store are bundles: one **base** APK with the code, plus **split** APKs for a processor
(`config.armeabi_v7a`, `config.arm64_v8a`), a screen density (`config.xhdpi`) and languages (`config.it`). A device
gets only the pieces it needs. Android installs a set of pieces as one app when they all have the same package name,
the same `versionCode` and the same signing certificate.

Each variant's base declares its own minimum Android version. On the Android versions where this was tried, the
installer checks that minimum on the base. And the base's list of required split types (`requiredSplitTypes`) is
only enforced by newer Android releases. So the base that accepts your Android, plus the native split for your
processor from the other variant, installs as one app.

`combine.py`:

1. reads every APK in two or more bundles (`.apkm`, `.apks`, `.xapk`, a folder of `.apk` files);
2. **stops** unless every piece is the same package, the same version and signed by the same certificate (checked
   with `apksigner`), and reports whether Google's source stamp, added to apps distributed through Google Play, is
   on each piece;
3. reads the TV over `adb` (Android version, processors, screen density, language), or takes them from flags;
4. picks the base with the lowest minimum your TV meets, the native split for your TV's processor, the screen density
   and your language;
5. prints the plan, and with `install` installs it in one install session.

It never downloads, edits or re-signs anything.

## Requirements

- Python 3.8+
- From the Android SDK build-tools: `apksigner` and `aapt2` (on `PATH`, under `$ANDROID_HOME`, or `--build-tools`)
- `adb`, with USB or network debugging on the TV (Settings > Device preferences > Developer options)
- The bundles of **the same version** of each variant, downloaded by you from a source that keeps the developer's
  original signatures

## Usage

See what it would do, reading the TV over adb:

```bash
python3 combine.py plan app-arm64.apkm app-armeabi-v7a.apkm
```

Or without a TV attached (`--sdk 30` is Android 11):

```bash
python3 combine.py plan app-arm64.apkm app-armeabi-v7a.apkm --sdk 30 --abi armeabi-v7a --density 320 --lang it
```

Example output:

```
device: Android API 30, processors armeabi_v7a, 320 dpi, languages it
app:    com.example.tv versionCode 230710200, 54 pieces read, one signer:
        SHA-256 416a4592ae2c583de3cc...
        Google source stamp on 54 of 54 pieces
plan:
  base                     app-arm64.apkm / base.apk  min API 29
  config.armeabi_v7a       app-armeabi-v7a.apkm / split_config.armeabi_v7a.apk  native armeabi-v7a
  config.xhdpi             app-arm64.apkm / split_config.xhdpi.apk
  config.it                app-arm64.apkm / split_config.it.apk
  config.en                app-arm64.apkm / split_config.en.apk
note:   the pieces come from different variants of the same version; every file is unchanged
```

Install:

```bash
python3 combine.py install app-arm64.apkm app-armeabi-v7a.apkm
```

Some TVs refuse an install from a computer that the Play Store would not have offered them
(`INSTALL_FAILED_VERIFICATION_FAILURE`). `--allow-unverified` turns the TV's "verify apps over USB" check off **for
that one install** and puts the setting back afterwards, even when the install fails. Use it only for pieces the tool
has verified as the developer's own.

Copy the chosen pieces to a folder instead, for another installer:

```bash
python3 combine.py extract app-arm64.apkm app-armeabi-v7a.apkm --out chosen/
```

When it cannot work, it says why and changes nothing:

```
stopped: no base APK accepts Android API 30: the lowest minimum among them is 32
stopped: no native split for the device's processors ['armeabi_v7a']: the bundles have ['arm64_v8a']
stopped: the bundles are different versions: [...] - download the same version of each variant
stopped: the pieces are not all signed by the same certificate: [...]
```

## When the app forces an update

The Play Store will not update an app it considers incompatible with your TV, and many streaming apps refuse to run
below a minimum version their server sets ("update required"). When that happens, download both variants of the
**new** version and run `install` again. With the same signature it installs over the old one as an update, keeping
your sign-in. Check first that the variant with the lower minimum still accepts your Android.

## Limits

- It works only while the developer publishes the variants and one base accepts your Android. If every base needs a
  newer Android, or no variant carries native code for your processor, there is nothing to combine.
- The code in the base may use features of a newer Android at runtime. If the base you combine came from a variant
  that declares your Android as supported, that is the developer's own claim, but test the app before relying on it.
- It does not get around copy protection, account or region checks, or a developer's minimum-version check.
- The developer does not support this use. Updates are manual.

## What this repository is not

- **No APKs here, ever.** They belong to their developers. `.gitignore` keeps them out.
- **No modified apps.** A file whose signature does not verify is refused.
- **No piracy or DRM circumvention.** You need your own subscription, and playback still goes through the app's own
  protection.

## Tried on

| App (package, version) | TV (model, Android, processor) | Result |
|---|---|---|
| NOW Italia for Android TV (`com.nowtv.it` 7.10.200) | Xiaomi MiTV-MSSP2, Android 11, armeabi-v7a | installs and starts |

Add a row with a pull request when you try it on another app or TV. Never include serial numbers, IP addresses or
account details.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Licence

MIT, see [LICENSE](LICENSE).
