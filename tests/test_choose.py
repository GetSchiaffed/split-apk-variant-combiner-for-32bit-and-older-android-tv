"""The choice of pieces, with made-up pieces: no APKs, SDK tools or device needed."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import combine  # noqa: E402


def piece(bundle, split=None, min_sdk=21, native=()):
    p = combine.Piece(f"/x/{bundle}/{split or 'base'}.apk", bundle)
    p.package, p.version, p.split, p.min_sdk, p.native = "com.example", "100", split, min_sdk, list(native)
    p.signer, p.stamp = "ab12", True
    return p


ARM64 = [piece("arm64", min_sdk=29), piece("arm64", "config.arm64_v8a", native=["arm64-v8a"]),
         piece("arm64", "config.xhdpi"), piece("arm64", "config.it"), piece("arm64", "config.en")]
V7A = [piece("v7a", min_sdk=32), piece("v7a", "config.armeabi_v7a", native=["armeabi-v7a"]),
       piece("v7a", "config.xhdpi"), piece("v7a", "config.it"), piece("v7a", "config.en")]
TV = combine.Device(30, ["armeabi_v7a", "armeabi"], 320, ["it"])


class Choose(unittest.TestCase):
    def test_the_base_one_variant_accepts_with_the_native_split_of_the_other(self):
        plan = combine.choose(ARM64 + V7A, TV)
        self.assertEqual(("arm64", 29), (plan[0].bundle, plan[0].min_sdk))
        self.assertEqual(("v7a", "config.armeabi_v7a"), (plan[1].bundle, plan[1].split))
        self.assertEqual(["config.xhdpi", "config.it", "config.en"], [p.split for p in plan[2:]])

    def test_no_base_for_the_device_android_stops(self):
        with self.assertRaisesRegex(combine.Stop, "no base APK accepts Android API 30"):
            combine.choose(V7A, TV)

    def test_no_native_split_for_the_device_processor_stops(self):
        with self.assertRaisesRegex(combine.Stop, "no native split"):
            combine.choose(ARM64, TV)

    def test_a_device_both_variants_fit_gets_one_variant_whole(self):
        phone = combine.Device(34, ["arm64_v8a", "armeabi_v7a"], 480, ["it"])
        plan = combine.choose(ARM64 + V7A, phone)
        self.assertEqual({"arm64"}, {p.bundle for p in plan[:2]})

    def test_nearest_density_and_english_kept(self):
        pieces = [piece("a", min_sdk=21), piece("a", "config.hdpi"), piece("a", "config.xxhdpi"), piece("a", "config.en")]
        plan = combine.choose(pieces, combine.Device(30, ["armeabi_v7a"], 320, ["de"]))
        self.assertEqual(["config.xxhdpi", "config.en"], [p.split for p in plan[1:]])


if __name__ == "__main__":
    unittest.main()
