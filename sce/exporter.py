# -*- coding: utf-8 -*-
"""Siril に読み込まれている画像から PDF を作る処理の本体（ダイアログからもコマンドからも使う）。

PixInsight 版 SkyChartExporter.js の SceExporter に相当する。PixInsight の
ImageWindow の代わりに sirilpy から次のものを読む。

  get_image_shape / get_image_fits_header / get_image_keywords … 画像の大きさ・WCS・撮影データ
  pix2radec                                                    … astropy で作った WCS の検証
  get_image_pixeldata(preview=True)                             … Siril のオートストレッチ済み画素
"""

import math
import os

import numpy as np
from PyQt6.QtCore import Qt

from . import __version__
from .catalog import Catalog
from .imageinfo import (AstropyTransform, SirilTransform, ImageInfo, NoSolutionError, build_kw, parse_header)
from .model import Model
from .options import Options
from . import photo as P
from .render import numpy_to_qimage, render_preview, write_pdf, Renderer
from .strings import tr, set_lang
from .astro import ang_dist


class SirilImage:
    """Siril の画像のスナップショット（ダイアログを開いている間に Siril 側が変わっても影響しない）"""

    def __init__(self, siril, log=print):
        self.s = siril
        self.log = log
        if not siril.is_image_loaded() or siril.is_sequence_loaded():
            raise NoSolutionError(tr("noImage"))
        shape = siril.get_image_shape()
        if not shape or len(shape) != 3:
            raise NoSolutionError(tr("noImage"))
        self.channels, self.height, self.width = (int(v) for v in shape)
        self.file_path = siril.get_image_filename() or ""
        text = siril.get_image_fits_header() or ""
        self.header = parse_header(text) if text.strip() else None
        try:
            self.keywords = siril.get_image_keywords()
        except Exception:  # noqa: BLE001
            self.keywords = None
        self.kw = build_kw(self.header, self.keywords)
        self.transform = self._make_transform()
        self._photo = {}

    def _make_transform(self):
        """astropy の WCS を作り、Siril 自身の座標変換（pix2radec）と中心・四隅で一致するか確かめる。
        一致しなければ Siril の座標変換を1点ずつ呼ぶ方式にする。"""
        w, h = self.width, self.height
        pts = [(w / 2, h / 2), (0, 0), (w, 0), (0, h), (w, h)]
        siril_pts = []
        for x, y in pts:
            try:
                r = self.s.pix2radec(x, y)
            except Exception:  # noqa: BLE001 未ソルブなら ValueError
                r = None
            if r is None or not all(math.isfinite(v) for v in r):
                if (x, y) == pts[0]:
                    raise NoSolutionError(tr("noSolution"))
                r = None
            siril_pts.append(r)
        if self.header is not None:
            try:
                t = AstropyTransform(self.header, w, h)
                scale = math.sqrt(abs(np.linalg.det(t.wcs.pixel_scale_matrix)))
                worst = 0.0
                for (x, y), r in zip(pts, siril_pts):
                    if r is None:
                        continue
                    ra, dec = t.to_sky(np.array([x]), np.array([y]))
                    worst = max(worst, ang_dist(float(ra[0]), float(dec[0]), r[0], r[1]))
                if worst <= max(0.5 * scale, 1 / 3600):
                    self.log("SkyChartExporter: WCS ok (astropy, max diff %.3f px)" % (worst / scale))
                    return t
                self.log("SkyChartExporter: astropy WCS differs from Siril by %.1f px; using Siril's transform"
                         % (worst / scale))
            except Exception as x:  # noqa: BLE001
                self.log("SkyChartExporter: astropy WCS unavailable (%s); using Siril's transform" % x)
        return SirilTransform(self.s, w, h)

    def info(self):
        name = os.path.splitext(os.path.basename(self.file_path))[0] if self.file_path else "image"
        return ImageInfo(self.transform, self.width, self.height, self.kw, file_path=self.file_path,
                         is_color=self.channels >= 3, image_id=name)

    def photo(self, mode):
        """表示向きの写真（QImage、元の画素数）。ストレッチ方式ごとにキャッシュする。"""
        if mode not in self._photo:
            arr = P.render_photo_from_siril(self.s, mode, self.log)
            self._photo = {mode: numpy_to_qimage(arr)}  # メモリ節約のため1つだけ持つ
        return self._photo[mode]


class Exporter:
    def __init__(self, siril, log=print, settings_dir="", siril_data_dir="", software=""):
        self.s = siril
        self.log = log
        self.settings_dir = settings_dir
        self.siril_data_dir = siril_data_dir
        self.software = software
        self.opts = Options()
        self.opts.load(settings_dir)
        self.catalog = Catalog(log=log)
        self.image = None

    def load_image(self):
        self.image = SirilImage(self.s, self.log)
        return self.image

    def build_model(self):
        set_lang(self.opts.lang)
        if self.image is None:
            self.load_image()
        self.catalog.load(use_stellarium=self.opts.useStellarium, stellarium_dir=self.opts.stellariumDir,
                          siril_data_dir=self.siril_data_dir, cache_dir=self.settings_dir)
        model = Model(self.image.info(), self.catalog, self.opts, software=self.software)
        # 画素サイズを手入力したら、そのカメラの値として覚えておく
        cam = model.info.camera
        if cam and self.opts.pixelOverride > 0 and self.opts.cameraPixelSizes.get(cam) != self.opts.pixelOverride:
            self.opts.cameraPixelSizes[cam] = self.opts.pixelOverride
            self.opts.save(self.settings_dir)
        return model

    def photo(self, long_px):
        full = self.image.photo(self.opts.stretch)
        native = max(full.width(), full.height())
        long_px = int(round(min(long_px, native)))
        if long_px >= native:
            return full
        return full.scaled(long_px, long_px, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)

    def export_pdf(self, file_path):
        import time
        model = self.build_model()
        t0 = time.time()
        r = write_pdf(file_path, model, self.photo, log=self.log)
        self.log("SkyChartExporter: %s (%.2f MB, %.1f s)" % (file_path, r["size"] / 1048576, time.time() - t0))
        return r, model

    def preview(self, width_px):
        model = self.build_model()
        renderer = Renderer(model)
        dpi = width_px / (renderer.W / 25.4)
        pw, ph = renderer.photo_size_mm()
        photo = self.photo(max(pw, ph) / 25.4 * dpi * 1.5)
        return render_preview(model, photo, width_px)

    def default_output_path(self, use_saved_dir=True):
        """既定の保存先。use_saved_dir なら前回保存したフォルダ、無ければ画像のフォルダ、Siril の作業フォルダの順"""
        img = self.image
        base = os.path.splitext(os.path.basename(img.file_path))[0] if (img and img.file_path) else "image"
        directory = (self.opts.outputDir if use_saved_dir else "") or \
            (os.path.dirname(img.file_path) if (img and img.file_path) else "")
        if not directory or not os.path.isdir(directory):
            try:
                directory = self.s.get_siril_wd()
            except Exception:  # noqa: BLE001
                directory = os.path.expanduser("~")
        return os.path.join(directory, base + "_skychart.pdf")

    def save_settings(self):
        self.opts.save(self.settings_dir)


def software_label():
    """フッターに記す処理ソフト名（Siril の版は Python から取得できないので sirilpy の版を記す）"""
    try:
        from sirilpy import __version__ as v
    except Exception:  # noqa: BLE001
        v = ""
    return "Siril" + (" (sirilpy %s)" % v if v and v != "unknown" else "")


__all__ = ["Exporter", "SirilImage", "software_label", "__version__"]
