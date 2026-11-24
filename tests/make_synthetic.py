# -*- coding: utf-8 -*-
"""試験用の合成画像（プレートソルブ済み・リニアな 32 ビット浮動小数点の FITS）を作る。

M42 付近を ASI2600（3.76 µm）と焦点距離 250 mm で撮った想定。恒星は Siril 同梱の
恒星表の位置に置くので、注記が星の上に来るかを目で確かめられる。WCS には SIP 歪み項も入れる。

  .venv/bin/python tests/make_synthetic.py tests/data/m42_linear.fit
（同時に 16 ビット版 m42_linear_u16.fit と、ストレッチ済みの m42_linear_stretched_u16.fit も作る）
"""
import csv
import math
import os
import sys

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

STARS = "/Applications/Siril.app/Contents/Resources/share/siril/catalogue/stars.csv"


def main(out):
    w, h = 3000, 2000
    ra0, dec0 = 83.82, -5.39
    scale = 206.264806 * 3.76 / 250 / 3600  # 度/px
    rot = math.radians(20)
    hdr = fits.Header()
    hdr["CTYPE1"], hdr["CTYPE2"] = "RA---TAN-SIP", "DEC--TAN-SIP"
    hdr["CRVAL1"], hdr["CRVAL2"] = ra0, dec0
    hdr["CRPIX1"], hdr["CRPIX2"] = w / 2 + 0.5, h / 2 + 0.5
    hdr["CD1_1"] = -scale * math.cos(rot)
    hdr["CD1_2"] = scale * math.sin(rot)
    hdr["CD2_1"] = scale * math.sin(rot)
    hdr["CD2_2"] = scale * math.cos(rot)
    hdr["A_ORDER"], hdr["B_ORDER"] = 2, 2
    hdr["A_2_0"], hdr["B_0_2"] = 2e-6, -1.5e-6
    hdr["RADESYS"] = "ICRS"
    hdr["EQUINOX"] = 2000.0
    wcs = WCS(hdr)

    rng = np.random.default_rng(1)
    img = np.full((3, h, w), 0.02, dtype=np.float32)
    img += rng.normal(0, 0.002, img.shape).astype(np.float32)
    yy, xx = np.mgrid[0:h, 0:w]
    # 星雲（M42 の位置に広がった光）
    mx, my = wcs.all_world2pix([[83.82, -5.39]], 0)[0]
    neb = np.exp(-(((xx - mx) ** 2 + (yy - my) ** 2) / (2 * 180.0 ** 2))).astype(np.float32)
    img[0] += 0.12 * neb
    img[1] += 0.06 * neb
    img[2] += 0.08 * neb
    with open(STARS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            ra, dec, mag = float(r["ra"]), float(r["dec"]), float(r["mag"])
            if abs(dec - dec0) > 3 or abs(ra - ra0) > 3:
                continue
            x, y = wcs.all_world2pix([[ra, dec]], 0)[0]
            if not (5 <= x < w - 5 and 5 <= y < h - 5):
                continue
            amp = min(0.9, 0.6 * 10 ** (-0.4 * (mag - 3)))
            s = 2.5
            x0, x1, y0, y1 = int(x) - 12, int(x) + 13, int(y) - 12, int(y) + 13
            g = amp * np.exp(-(((xx[y0:y1, x0:x1] - x) ** 2 + (yy[y0:y1, x0:x1] - y) ** 2) / (2 * s * s)))
            img[:, y0:y1, x0:x1] += g.astype(np.float32)
    img = np.clip(img, 0, 1)
    hdr["EXPTIME"] = (180.0, "[s] Exposure time of one frame")
    hdr["STACKCNT"] = (40, "Stack frames")
    hdr["LIVETIME"] = (7200.0, "[s] Exposure time after deadtime correction")
    hdr["INSTRUME"] = "ZWO ASI2600MC Pro"
    hdr["TELESCOP"] = "RedCat 51"
    hdr["FOCALLEN"] = 250.0
    hdr["XPIXSZ"] = 3.76
    hdr["YPIXSZ"] = 3.76
    hdr["FILTER"] = "L-Pro"
    hdr["DATE-OBS"] = "2025-12-20T14:05:00"
    hdr["SITELAT"] = 43.0614
    hdr["SITELONG"] = 141.3542
    hdr["OBJECT"] = "M42"
    fits.PrimaryHDU(img, header=hdr).writeto(out, overwrite=True)
    print("wrote", out, img.shape)
    # 16 ビット版（リニアのまま / ストレッチ済み）: 自動ストレッチの判定の確認用
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from sce.photo import autostretch
    base = os.path.splitext(out)[0]
    for name, data in (("_u16.fit", img), ("_stretched_u16.fit", autostretch(img, linked=True))):
        u16 = np.round(np.clip(data, 0, 1) * 65535).astype(np.uint16)
        hdu = fits.PrimaryHDU(u16, header=hdr)  # astropy は BZERO=32768 で符号なし 16 ビットとして書く
        hdu.writeto(base + name, overwrite=True)
        print("wrote", base + name)


if __name__ == "__main__":
    os.makedirs(os.path.dirname(os.path.abspath(sys.argv[1])), exist_ok=True)
    main(sys.argv[1])
