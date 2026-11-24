# -*- coding: utf-8 -*-
"""プレートソルブ済み画像からの情報抽出（中心座標・視野・焦点距離・撮影データ）と
画像内の天体の判定。PixInsight 版 SCE-ImageData.jsh の移植。

座標は Siril の表示座標で扱う: 原点は画像の左上の角、x は右、y は下向き
（sirilpy の pix2radec / radec2pix と同じ）。Siril は画素配列の 0 行目を画面の
下端に表示するので、FITS の 1 始まりの画素中心 (X, Y) との関係は
    x = X − 0.5、 y = H − Y + 0.5
となる（Siril 1.4.5 src/algos/siril_wcs.c の pix2wcs2 と siril_pythoncommands.c）。
"""

import math
import re

import numpy as np

from .astro import ang_dist, ang_dist_np, position_angle, parse_datetime, parse_sexagesimal, D2R
from .cameras import pixel_size_for_camera


class NoSolutionError(Exception):
    pass


def parse_header(text):
    """Siril の get_image_fits_header() の文字列 → astropy の Header"""
    from astropy.io import fits
    # sirilpy の共有メモリの文字列は C の NUL 終端を含む（取り除かないと END 以降の扱いが狂う）
    text = (text or "").rstrip("\x00")
    return fits.Header.fromstring(text, sep="\n" if "\n" in text else "")


class AstropyTransform:
    """astropy.wcs による座標変換（配列で一括処理。SIP 歪み補正に対応）"""

    def __init__(self, header, width, height):
        import warnings
        from astropy.wcs import WCS, FITSFixedWarning
        h = header.copy()
        h["NAXIS"] = 2
        h["NAXIS1"] = width
        h["NAXIS2"] = height
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FITSFixedWarning)
            self.wcs = WCS(h, naxis=2)
        if not self.wcs.has_celestial:
            raise NoSolutionError("no celestial WCS")
        m = self.wcs.pixel_scale_matrix
        if not np.isfinite(m).all() or abs(np.linalg.det(m)) < 1e-20:
            raise NoSolutionError("invalid WCS matrix")
        self.W = width
        self.H = height
        self.name = "astropy"

    def to_sky(self, x, y):
        """表示座標 → (ra, dec)。配列可。"""
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        ra, dec = self.wcs.all_pix2world(x - 0.5, self.H - y - 0.5, 0)
        return np.mod(ra, 360.0), dec

    def to_image(self, ra, dec):
        """(ra, dec) → 表示座標。配列可。変換できない点は NaN。"""
        ra = np.asarray(ra, dtype=float)
        dec = np.asarray(dec, dtype=float)
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                px, py = self.wcs.all_world2pix(ra, dec, 0, quiet=True, maxiter=30)
            except Exception:  # noqa: BLE001 収束しない場合は線形部分だけで
                px, py = self.wcs.wcs_world2pix(ra, dec, 0)
        return px + 0.5, self.H - py - 0.5

    def is_flipped(self):
        cd = self.wcs.pixel_scale_matrix
        return float(np.linalg.det(cd)) > 0


class SirilTransform:
    """sirilpy の pix2radec / radec2pix を1点ずつ呼ぶ変換（astropy で扱えない WCS 用の予備）"""

    def __init__(self, siril, width, height):
        self.s = siril
        self.W = width
        self.H = height
        self.name = "siril"

    def to_sky(self, x, y):
        x = np.atleast_1d(np.asarray(x, dtype=float))
        y = np.atleast_1d(np.asarray(y, dtype=float))
        ra = np.full(x.shape, np.nan)
        dec = np.full(x.shape, np.nan)
        for i in range(x.size):
            try:
                r = self.s.pix2radec(float(x.flat[i]), float(y.flat[i]))
                if r:
                    ra.flat[i], dec.flat[i] = r
            except Exception:  # noqa: BLE001
                pass
        return ra, dec

    def to_image(self, ra, dec):
        ra = np.atleast_1d(np.asarray(ra, dtype=float))
        dec = np.atleast_1d(np.asarray(dec, dtype=float))
        x = np.full(ra.shape, np.nan)
        y = np.full(ra.shape, np.nan)
        for i in range(ra.size):
            try:
                r = self.s.radec2pix(float(ra.flat[i]), float(dec.flat[i]))
                if r:
                    x.flat[i], y.flat[i] = r
            except Exception:  # noqa: BLE001
                pass
        return x, y

    def is_flipped(self):
        # 中心から北と東へ少し進んだ方向の外積で判定（表示座標は y 下向き）
        cx, cy = self.W / 2, self.H / 2
        ra, dec = self.to_sky(np.array([cx]), np.array([cy]))
        d = 0.01
        xn, yn = self.to_image(ra, np.minimum(89.99, dec + d))
        xe, ye = self.to_image(ra + d / max(0.05, math.cos(dec[0] * D2R)), dec)
        cross = (xn[0] - cx) * (ye[0] - cy) - (yn[0] - cy) * (xe[0] - cx)
        # 通常の空（北が上なら東が左）は表示座標で cross > 0
        return cross < 0


class ImageInfo:
    """kw: FITS キーワード名 → 値（文字列・数値）の辞書"""

    def __init__(self, transform, width, height, kw, file_path="", is_color=True, image_id="image"):
        self.t = transform
        self.width = width
        self.height = height
        self.kw = {k.upper(): v for k, v in (kw or {}).items()}
        self.file_path = file_path or ""
        self.is_color = is_color
        self.id = image_id
        self.compute_geometry()
        self.read_acquisition_data()

    # ------------------------------------------------------------------------

    def kw_num(self, *names):
        for n in names:
            v = self.kw.get(n)
            if v is None or v == "":
                continue
            try:
                x = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(x):
                return x
        return None

    def kw_str(self, *names):
        for n in names:
            v = self.kw.get(n)
            if v is None:
                continue
            s = str(v).strip().strip("'").strip()
            if s:
                return s
        return ""

    def to_sky(self, x, y):
        ra, dec = self.t.to_sky(np.array([x], dtype=float), np.array([y], dtype=float))
        if not (np.isfinite(ra[0]) and np.isfinite(dec[0])):
            return None
        return float(ra[0]), float(dec[0])

    def to_image(self, ra, dec):
        x, y = self.t.to_image(np.array([ra], dtype=float), np.array([dec], dtype=float))
        if not (np.isfinite(x[0]) and np.isfinite(y[0])):
            return None
        return float(x[0]), float(y[0])

    # ------------------------------------------------------------------------

    def compute_geometry(self):
        w, h = self.width, self.height
        cx, cy = w / 2, h / 2
        c = self.to_sky(cx, cy)
        if c is None:
            raise NoSolutionError("The astrometric solution cannot be evaluated at the image center.")
        self.ra, self.dec = c

        # 画素スケール（″/px）
        d = max(2, min(100, min(w, h) / 4))
        a1, a2 = self.to_sky(cx - d / 2, cy), self.to_sky(cx + d / 2, cy)
        b1, b2 = self.to_sky(cx, cy - d / 2), self.to_sky(cx, cy + d / 2)
        sx = ang_dist(a1[0], a1[1], a2[0], a2[1]) / d
        sy = ang_dist(b1[0], b1[1], b2[0], b2[1]) / d
        self.scale_deg = (sx + sy) / 2
        self.scale_arcsec = self.scale_deg * 3600

        # 視野（中心を通る線に沿って積算）
        def path_length(x0, y0, x1, y1, n=16):
            xs = np.array([x0 + (x1 - x0) * i / n for i in range(n + 1)])
            ys = np.array([y0 + (y1 - y0) * i / n for i in range(n + 1)])
            ra, dec = self.t.to_sky(xs, ys)
            total = 0.0
            for i in range(n):
                if np.isfinite(ra[i]) and np.isfinite(ra[i + 1]):
                    total += ang_dist(ra[i], dec[i], ra[i + 1], dec[i + 1])
            return total

        self.fov_w = path_length(0, cy, w, cy)
        self.fov_h = path_length(cx, 0, cx, h)

        # 対角の視野（35mm判換算の焦点距離の推定に使う）
        c0, c1 = self.to_sky(0, 0), self.to_sky(w, h)
        self.fov_diag = ang_dist(c0[0], c0[1], c1[0], c1[1]) if (c0 and c1) else math.hypot(self.fov_w, self.fov_h)

        # 中心から最も遠い画像上の点までの角距離
        self.radius = 0.0
        for px, py in ((0, 0), (w, 0), (0, h), (w, h), (cx, 0), (cx, h), (0, cy), (w, cy)):
            s = self.to_sky(px, py)
            if s:
                self.radius = max(self.radius, ang_dist(self.ra, self.dec, s[0], s[1]))

        # 画像の外周（天球座標）
        n = 24
        edge = ([(w * i / n, 0) for i in range(n)] + [(w, h * i / n) for i in range(n)] +
                [(w * (n - i) / n, h) for i in range(n)] + [(0, h * (n - i) / n) for i in range(n)])
        ra, dec = self.t.to_sky(np.array([p[0] for p in edge]), np.array([p[1] for p in edge]))
        self.bounds_rd = [(float(a), float(b)) for a, b in zip(ra, dec) if np.isfinite(a) and np.isfinite(b)]

        # 画像の「上」方向の位置角と鏡像の判定
        dd = min(50, h / 4)
        up = self.to_sky(cx, cy - dd)
        self.up_pa = position_angle(self.ra, self.dec, up[0], up[1])
        self.flipped = bool(self.t.is_flipped())

        # Siril のプレートソルブ結果と同じ定義の回転角:
        # 北へ 10″ 進んだ点の方向を、画像の上方向から時計回りに測る（"Up is X deg CounterClockwise wrt. N"）
        self.rotation = None
        if 90 - abs(self.dec) >= 2.78e-3:
            pn = self.to_image(self.ra, self.dec + 2.78e-3)
            if pn:
                xn = pn[0] - w / 2
                yn = h / 2 - pn[1]
                rot = math.degrees(math.atan2(xn, yn))
                self.rotation = rot + 360 if rot < 0 else rot
        self.reference_system = self.kw_str("RADESYS") or "ICRS"

    def read_acquisition_data(self):
        # 焦点距離（記録値）と画素サイズ（µm）・ビニング
        f = self.kw_num("FOCALLEN", "FOCAL")
        self.focal_nominal = f if (f and f > 0) else None
        p = self.kw_num("XPIXSZ", "PIXSIZE1", "PIXSIZE")
        self.binning = 1
        xb = self.kw_num("XBINNING")
        if xb and xb > 1:
            self.binning = int(xb)
        # Siril は XPIXSZ にビニング込みの値を書くことがあるので、そのまま使い、ビニングは掛けない
        self.pixel_size = p if (p and p > 0) else None
        self.pixel_includes_binning = True
        self.focal_effective = None

        # 撮影日時
        self.date_obs = parse_datetime(self.kw.get("DATE-BEG")) or parse_datetime(self.kw.get("DATE-OBS"))

        # 露出: 1枚あたり × 枚数（Siril のスタックは STACKCNT / LIVETIME、PixInsight は NCOMBINE）
        self.exposure = self.kw_num("EXPTIME", "EXPOSURE")
        n = self.kw_num("STACKCNT", "NCOMBINE")
        self.n_subs = int(n) if n and n > 1 else None
        self.livetime = self.kw_num("LIVETIME")

        self.telescope = self.kw_str("TELESCOP")
        self.camera = self.kw_str("INSTRUME")
        self.filter = self.kw_str("FILTER")
        self.object = self.kw_str("OBJECT")
        self.observer = self.kw_str("OBSERVER")

        # 撮影地
        self.site_lat = self.site_lon = None
        la = parse_sexagesimal(self.kw.get("SITELAT") or self.kw.get("OBSGEO-B") or "")
        lo = parse_sexagesimal(self.kw.get("SITELONG") or self.kw.get("OBSGEO-L") or "")
        if la is not None and lo is not None and not (la == 0 and lo == 0):
            self.site_lat, self.site_lon = la, lo

    def resolve_focal(self, opts):
        """焦点距離を決める（手入力 → 記録値 → 画素サイズと画素スケール → 視野から35mm判換算）。

        焦点距離[mm] = 206.265 × 画素サイズ[µm] ÷ 画素スケール[″/px]
        画素サイズは 手入力 → 画像の記録 → カメラごとの前回入力値 → カメラ名からの推定 の順に探す。
        結果: self.focal = {"value", "source", "px", "pxSource", "equiv35"}
        """
        cam = self.camera
        px, px_source = 0.0, ""
        if opts.pixelOverride > 0:
            px, px_source = opts.pixelOverride, "manual"
        elif self.pixel_size:
            px, px_source = self.pixel_size, "header"
        elif cam and (opts.cameraPixelSizes or {}).get(cam, 0) > 0:
            px, px_source = opts.cameraPixelSizes[cam], "remembered"
        else:
            v = pixel_size_for_camera(cam) if cam else 0.0
            if v > 0:
                px, px_source = v, "camera"
        # 記録値（XPIXSZ）はビニング込みとみなす。手入力・推定値はセンサーの画素なのでビニングを掛ける
        bin_k = 1 if px_source == "header" else self.binning
        self.focal_effective = 206.264806 * px * bin_k / self.scale_arcsec if (px > 0 and self.scale_arcsec > 0) else None
        f = {"value": None, "source": "", "px": px, "pxSource": px_source}
        if opts.focalOverride > 0:
            f["value"], f["source"] = opts.focalOverride, "manual"
        elif self.focal_nominal:
            f["value"], f["source"] = self.focal_nominal, "header"
        elif self.focal_effective:
            f["value"], f["source"] = self.focal_effective, "computed"
        # 35mm判換算（対角 43.27 mm のセンサーで同じ対角視野になる焦点距離）
        half = min(89, self.fov_diag / 2) * D2R
        f["equiv35"] = 43.267 / (2 * math.tan(half)) if half > 0 else None
        if not f["value"] and f["equiv35"]:
            f["value"], f["source"] = f["equiv35"], "fov35"
        self.focal = f
        return f

    # ------------------------------------------------------------------------

    def find_in_image(self, objects, size_of=None):
        """天体の配列を画像座標に変換し、画像に写っているものだけを返す。返り値: [(o, x, y)]"""
        if not objects:
            return []
        ras = np.array([o["ra"] for o in objects], dtype=float)
        decs = np.array([o["dec"] for o in objects], dtype=float)
        sizes = np.array([(size_of(o) if size_of else 0.0) or 0.0 for o in objects], dtype=float)
        dist = ang_dist_np(self.ra, self.dec, ras, decs)
        idx = np.nonzero(dist <= self.radius + sizes / 2 + 0.02)[0]
        if idx.size == 0:
            return []
        x, y = self.t.to_image(ras[idx], decs[idx])
        rpx = sizes[idx] / 2 / self.scale_deg
        w, h = self.width, self.height
        ok = np.isfinite(x) & np.isfinite(y)
        inside = ok & (x >= -rpx) & (y >= -rpx) & (x < w + rpx) & (y < h + rpx)
        sel = np.nonzero(inside)[0]
        if sel.size == 0:
            return []
        # 外挿による誤検出の防止（往復変換の一致を確認）
        bra, bdec = self.t.to_sky(x[sel], y[sel])
        tol = max(10 * self.scale_deg, 1 / 60)
        result = []
        for j, k in enumerate(sel):
            i = idx[k]
            if not (np.isfinite(bra[j]) and np.isfinite(bdec[j])):
                continue
            if ang_dist(bra[j], bdec[j], ras[i], decs[i]) > tol:
                continue
            result.append((objects[i], float(x[k]), float(y[k])))
        return result


def build_kw(header=None, keywords=None):
    """astropy の Header と sirilpy の FKeywords から、キーワード名 → 値 の辞書を作る"""
    kw = {}
    if header is not None:
        for card in header.cards:
            k = card.keyword
            if k and k not in ("COMMENT", "HISTORY", "") and k not in kw:
                kw[k] = card.value
    if keywords is not None:
        def put(name, value, valid=lambda v: v not in (None, "", 0, 0.0)):
            if name not in kw or kw[name] in (None, ""):
                if valid(value):
                    kw[name] = value
        g = lambda a: getattr(keywords, a, None)  # noqa: E731
        put("EXPTIME", g("exposure"))
        put("LIVETIME", g("livetime"))
        put("STACKCNT", g("stackcnt"))
        put("FOCALLEN", g("focal_length"))
        put("XPIXSZ", g("pixel_size_x"))
        put("XBINNING", g("binning_x"))
        put("TELESCOP", g("telescop"))
        put("INSTRUME", g("instrume"))
        put("FILTER", g("filter"))
        put("OBJECT", g("object"))
        put("OBSERVER", g("observer"))
        d = g("date_obs")
        if d is not None and "DATE-OBS" not in kw:
            kw["DATE-OBS"] = d.isoformat() if hasattr(d, "isoformat") else str(d)
        if g("sitelat") not in (None, 0, 0.0) and "SITELAT" not in kw:
            kw["SITELAT"] = g("sitelat")
            kw["SITELONG"] = g("sitelong")
    return kw


def has_celestial_wcs(header):
    return any(re.match(r"^(CTYPE1|CD1_1|PC1_1|CDELT1)$", k or "") for k in header.keys()) and \
        str(header.get("CTYPE1", "")).startswith("RA")
