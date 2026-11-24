# -*- coding: utf-8 -*-
"""単体テスト（Siril 不要）。numpy・astropy が必要。PyQt6 があれば描画も試す。

  .venv/bin/python -m unittest discover -s tests -v
"""
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import numpy as np  # noqa: E402

from sce import astro as A  # noqa: E402
from sce import catalog as C  # noqa: E402
from sce import photo as P  # noqa: E402
from sce import stellarium as S  # noqa: E402
from sce.cameras import pixel_size_for_camera  # noqa: E402
from sce.options import Options  # noqa: E402
from sce.strings import fmt, tr  # noqa: E402

SIRIL_CAT = C.find_siril_catalogue_dir()
STEL_DIR = S.find_stellarium_dir()


def tan_header(ra0, dec0, w, h, scale_arcsec, rot_deg=0.0, flip=False):
    from astropy.io import fits
    s = scale_arcsec / 3600
    r = math.radians(rot_deg)
    hd = fits.Header()
    hd["CTYPE1"], hd["CTYPE2"] = "RA---TAN", "DEC--TAN"
    hd["CRVAL1"], hd["CRVAL2"] = ra0, dec0
    hd["CRPIX1"], hd["CRPIX2"] = w / 2 + 0.5, h / 2 + 0.5
    sx = s if flip else -s
    hd["CD1_1"], hd["CD1_2"] = sx * math.cos(r), s * math.sin(r)
    hd["CD2_1"], hd["CD2_2"] = -sx * math.sin(r), s * math.cos(r)
    return hd


class TestAstro(unittest.TestCase):
    def test_format(self):
        self.assertEqual(A.format_ra(83.8221), "05h 35m 17.3s")
        self.assertEqual(A.format_dec(-5.3911), "−05° 23′ 28″")
        self.assertEqual(A.format_angle(2.5), "2.50°")
        self.assertEqual(A.format_angle(0.5), "30.0′")
        self.assertEqual(A.format_ra_short(82.5), "5h30m")
        self.assertEqual(A.format_dec_short(-5.5), "−5°30′")
        self.assertEqual(A.format_duration(7200), "2h 00m")
        self.assertEqual(A.format_duration(330), "5m 30s")
        self.assertEqual(A.format_duration(30), "30s")

    def test_geometry(self):
        self.assertAlmostEqual(A.ang_dist(10, 20, 10, 21), 1.0, places=9)
        ra, dec = A.offset_point(10, 20, 90, 1.0)
        self.assertAlmostEqual(A.ang_dist(10, 20, ra, dec), 1.0, places=9)
        self.assertGreater(ra, 10)  # 位置角 90° は東（赤経が増える）
        self.assertAlmostEqual(A.position_angle(10, 20, 10, 21), 0.0, places=6)

    def test_datetime(self):
        d = A.parse_datetime("2025-12-20T14:05:00")
        self.assertEqual(A.format_datetime(d), "2025-12-20 14:05 UTC")
        self.assertIsNone(A.parse_datetime(""))
        self.assertAlmostEqual(A.parse_sexagesimal("+35 30 00"), 35.5)
        self.assertAlmostEqual(A.parse_sexagesimal("-0:30:00"), -0.5)


class TestStrings(unittest.TestCase):
    def test_fmt(self):
        self.assertEqual(fmt("%.2f″/px", 1.234), "1.23″/px")
        self.assertEqual(fmt("ほか %d 天体", 12), "ほか 12 天体")
        self.assertEqual(tr("rRA", "en"), "RA")
        self.assertEqual(tr("rRA", "ja"), "赤経")


class TestStarsAndCameras(unittest.TestCase):
    def test_parse_star_id(self):
        self.assertEqual(C.parse_star_id("alf CMa"), ("α CMa", "CMA"))
        self.assertEqual(C.parse_star_id("bet01 Cyg"), ("β¹ Cyg", "CYG"))
        self.assertEqual(C.parse_star_id("alf Cen A"), ("α Cen A", "CEN"))
        self.assertEqual(C.parse_star_id("61 Cyg"), ("61 Cyg", "CYG"))
        self.assertEqual(C.pick_proper_name("Dog Star/Sirius/Sirius A"), "Sirius")

    def test_cameras(self):
        self.assertEqual(pixel_size_for_camera("ZWO ASI2600MC Pro"), 3.76)
        self.assertEqual(pixel_size_for_camera("NIKON D810"), 4.88)
        self.assertEqual(pixel_size_for_camera("Unknown cam"), 0.0)


@unittest.skipUnless(SIRIL_CAT, "Siril の同梱カタログが見つからない")
class TestSirilCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = C.Catalog(log=lambda *a: None)
        cls.cat.load(use_stellarium=False)
        cls.by = {}
        for o in cls.cat.dso:
            for k in (o.get("m"), o.get("ngc"), o.get("key")):
                if k:
                    cls.by.setdefault(k, o)

    def test_merge(self):
        m42 = self.by["M42"]
        self.assertIs(m42, self.by["NGC1976"])  # Messier と NGC は1件に統合
        self.assertEqual(m42["type"], "emissionNebula")
        self.assertEqual(m42["ja"], "オリオン大星雲")
        self.assertIn("Sh2-244", C.dso_alt_ids(self.by["M1"]))
        # 別名で重複する NGC（NGC6 = NGC20）は1件にまとめる
        self.assertIn("NGC6", self.by)
        self.assertNotIn("NGC20", self.by)
        self.assertIn("NGC4236", self.by)  # "NGC4236_1" → 本体

    def test_stars_and_constellations(self):
        self.assertEqual(self.cat.stars[0]["proper"], "Sirius")
        self.assertEqual(self.cat.stars[0]["ja"], "シリウス")
        self.assertGreater(len(self.cat.const_lines), 700)
        self.assertIn("ORI", self.cat.const_centers)

    def test_find_constellation(self):
        f = C.Catalog.find_constellation
        self.assertEqual(f(83.82, -5.39), "ORI")      # M42
        self.assertEqual(f(10.68, 41.27), "AND")      # M31
        self.assertEqual(f(37.95, 89.26), "UMI")      # 北極星
        self.assertEqual(f(279.23, 38.78), "LYR")     # ベガ


@unittest.skipUnless(STEL_DIR, "Stellarium が見つからない")
class TestStellarium(unittest.TestCase):
    def test_dso(self):
        r = S.load_dso(STEL_DIR)
        self.assertGreater(len(r["list"]), 50000)
        by = {x["key"]: x for x in r["list"]}
        m42 = by["M:42"]
        self.assertAlmostEqual(m42["ra"], 83.82, delta=0.05)
        self.assertEqual(m42["desig"], "M42")
        self.assertIn("NGC 1976", m42["alt"])

    def test_boundaries(self):
        b = S.load_boundaries(STEL_DIR)
        self.assertGreater(len(b), 700)
        # 境界上の点は、境界の両側どちらかの星座に属する（歳差補正の確認）
        bad = 0
        n = 0
        for e in b[::40]:
            ra, dec = e["pts"][len(e["pts"]) // 2]
            c = C.Catalog.find_constellation(ra, dec)
            n += 1
            if c not in (e["c1"], e["c2"]) and not (c.startswith("SER") and "SER" in e["c1"] + e["c2"]):
                bad += 1
        self.assertLessEqual(bad, max(1, n // 10))


class TestImageInfo(unittest.TestCase):
    def test_transform_convention(self):
        from sce.imageinfo import AstropyTransform, ImageInfo
        hd = tan_header(83.82, -5.39, 3000, 2000, 3.1, rot_deg=20)
        t = AstropyTransform(hd, 3000, 2000)
        ra, dec = t.to_sky(np.array([1500.0]), np.array([1000.0]))
        self.assertAlmostEqual(float(ra[0]), 83.82, places=6)
        self.assertAlmostEqual(float(dec[0]), -5.39, places=6)
        x, y = t.to_image(np.array([83.82]), np.array([-5.39 + 0.1]))
        self.assertLess(y[0], 1000)  # 北は（回転が小さければ）画面の上
        info = ImageInfo(t, 3000, 2000, {"FOCALLEN": 250, "XPIXSZ": 3.76, "EXPTIME": 180, "STACKCNT": 40})
        self.assertAlmostEqual(info.scale_arcsec, 3.1, places=2)
        self.assertAlmostEqual(info.rotation, 20.0, places=1)
        self.assertFalse(info.flipped)
        o = Options()
        f = info.resolve_focal(o)
        self.assertEqual(f["source"], "header")
        self.assertAlmostEqual(info.focal_effective, 250.0, delta=1.0)
        found = info.find_in_image([{"ra": 83.82, "dec": -5.39}, {"ra": 90, "dec": 20}])
        self.assertEqual(len(found), 1)

    def test_flipped(self):
        from sce.imageinfo import AstropyTransform, ImageInfo
        t = AstropyTransform(tan_header(10, 40, 1000, 800, 5, flip=True), 1000, 800)
        self.assertTrue(ImageInfo(t, 1000, 800, {}).flipped)


class TestPhotoAndOptions(unittest.TestCase):
    def test_autostretch(self):
        rng = np.random.default_rng(0)
        d = np.clip(0.02 + rng.normal(0, 0.002, (3, 200, 300)), 0, 1).astype(np.float32)
        s = P.autostretch(d, linked=True)
        self.assertAlmostEqual(float(np.median(s)), 0.25, delta=0.03)  # 背景が目標 0.25 になる
        u8 = P.to_display_uint8(s)
        self.assertEqual(u8.shape, (200, 300, 3))
        self.assertTrue(u8.flags["C_CONTIGUOUS"])

    def test_flip(self):
        a = np.zeros((1, 3, 2), dtype=np.uint8)
        a[0, 0, :] = 255  # 0 行目 = Siril では画面の下端
        out = P.flip_to_display(a)
        self.assertEqual(out[-1, 0], 255)
        self.assertEqual(out[0, 0], 0)

    def test_options_roundtrip(self):
        o = Options()
        o.apply_preset("detailed")
        o.title = "一時的なタイトル"
        o.observer = "観測者"
        d = tempfile.mkdtemp()
        self.assertTrue(o.save(d))
        p = Options()
        p.load(d)
        self.assertEqual(p.matching_preset(), "detailed")
        self.assertEqual(p.observer, "観測者")
        self.assertEqual(p.title, "")  # タイトルは保存しない


if __name__ == "__main__":
    unittest.main()
