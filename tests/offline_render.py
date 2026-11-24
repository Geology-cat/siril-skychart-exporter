# -*- coding: utf-8 -*-
"""Siril を使わずに FITS ファイルから PDF とプレビュー PNG を作る開発用スクリプト。

  .venv/bin/python tests/offline_render.py 入力.fit 出力.pdf [--lang en] [--preset detailed] [--memo ...]
"""
import argparse
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import numpy as np  # noqa: E402
from astropy.io import fits  # noqa: E402
from PyQt6.QtGui import QGuiApplication  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402

from sce.catalog import Catalog  # noqa: E402
from sce.imageinfo import AstropyTransform, ImageInfo, build_kw  # noqa: E402
from sce.model import Model  # noqa: E402
from sce.options import Options  # noqa: E402
from sce import photo as P  # noqa: E402
from sce.render import numpy_to_qimage, render_preview, write_pdf  # noqa: E402
from sce.strings import set_lang  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fits")
    ap.add_argument("out")
    ap.add_argument("--lang", default="ja")
    ap.add_argument("--preset", default="standard")
    ap.add_argument("--memo", default="")
    ap.add_argument("--page", default="A4")
    ap.add_argument("--theme", default="light")
    ap.add_argument("--no-stellarium", action="store_true")
    ap.add_argument("--stretch", default="auto")
    a = ap.parse_args()
    app = QGuiApplication(sys.argv[:1])  # noqa: F841
    with fits.open(a.fits) as hdul:
        header = hdul[0].header
        data = hdul[0].data
    chw = P.as_chw(data)
    c, h, w = chw.shape
    opts = Options()
    opts.lang, opts.memo, opts.pageSize, opts.theme = a.lang, a.memo, a.page, a.theme
    opts.apply_preset(a.preset)
    opts.useStellarium = not a.no_stellarium
    set_lang(opts.lang)
    cat = Catalog()
    cat.load(use_stellarium=opts.useStellarium, cache_dir=os.path.join(os.path.dirname(a.out), ".cache"))
    t = AstropyTransform(header, w, h)
    info = ImageInfo(t, w, h, build_kw(header), file_path=a.fits, is_color=c >= 3)
    model = Model(info, cat, opts, software="offline test")
    bitpix = 8 if header.get("BITPIX") == 8 else None
    data01 = P.normalize(chw, bitpix)
    auto_linear = a.stretch == "auto" and bitpix != 8 and P.is_linear([float(np.median(x)) for x in data01])
    if a.stretch in ("linked", "unlinked") or auto_linear:
        data01 = P.autostretch(data01, linked=a.stretch != "unlinked")
    full = numpy_to_qimage(P.to_display_uint8(data01))

    def src(long_px):
        if long_px >= max(full.width(), full.height()):
            return full
        return full.scaled(long_px, long_px, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

    r = write_pdf(a.out, model, src)
    print("PDF", r)
    prev = render_preview(model, src(1600), 1400)
    prev.save(os.path.splitext(a.out)[0] + "-preview.png")
    print("title:", model.title)
    print("objects:", len(model.list_items), [i["desig"] for i in model.list_items[:12]])
    print("rows:", model.info_rows)


if __name__ == "__main__":
    main()
