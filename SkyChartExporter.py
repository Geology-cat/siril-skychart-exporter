# -*- coding: utf-8 -*-
"""
SkyChartExporter for Siril

プレートソルブ済みの画像から、次の内容を1枚の横長PDFにまとめて書き出す Siril 用 Python スクリプト。

  ・アノテーションを書き込んだ写真（書き込み量はプリセット/個別に選択）
  ・天球上の位置を示す星図（撮影範囲を枠で表示、範囲は自由に指定）
  ・基本データ（中心座標・星座・視野・焦点距離・回転角・撮影データ）
  ・写っている天体とその種類の一覧
  ・メモ（ダイアログで記入）

PixInsight 用 SkyChartExporter（https://github.com/Geology-cat/SkyChartExporter）の Siril 移植版。
必要環境: Siril 1.4.0 以上（sirilpy）。PyQt6 と astropy は初回実行時に自動で導入する。

使い方:
  GUI: スクリプト → Pythonスクリプト → SkyChartExporter
  コマンド（ダイアログなし）:
    pyscript SkyChartExporter.py --out ~/Desktop/m42.pdf --preset detailed --memo "メモ"
"""

import argparse
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

SCRIPT_NAME = "SkyChartExporter"


def parse_args(argv):
    ap = argparse.ArgumentParser(prog=SCRIPT_NAME, description="Annotated sky chart PDF for plate-solved images")
    ap.add_argument("--out", help="PDF の保存先。指定するとダイアログを出さずに書き出す")
    ap.add_argument("--lang", choices=["ja", "en"])
    ap.add_argument("--preset", choices=["none", "minimal", "standard", "detailed", "all"])
    ap.add_argument("--page", choices=["A4", "A3", "Letter", "Wide"])
    ap.add_argument("--theme", choices=["light", "dark"])
    ap.add_argument("--stretch", choices=["auto", "none", "linked", "unlinked"])
    ap.add_argument("--title")
    ap.add_argument("--memo")
    ap.add_argument("--observer")
    ap.add_argument("--site")
    ap.add_argument("--font")
    ap.add_argument("--dpi", type=float)
    ap.add_argument("--max-mb", type=float)
    ap.add_argument("--no-compress", action="store_true")
    ap.add_argument("--focal", type=float, help="焦点距離 (mm)")
    ap.add_argument("--pixel", type=float, help="画素サイズ (µm)")
    ap.add_argument("--chart-fov", type=float, help="星図の範囲（度）")
    ap.add_argument("--no-stellarium", action="store_true")
    ap.add_argument("--stellarium-dir")
    return ap.parse_args(argv)


def apply_args(opts, a):
    if a.lang:
        opts.lang = a.lang
    if a.preset:
        opts.apply_preset(a.preset)
    if a.page:
        opts.pageSize = a.page
    if a.theme:
        opts.theme = a.theme
    if a.stretch:
        opts.stretch = a.stretch
    # タイトルとメモだけ "\n" を改行にする（パスの "\n" を壊さないため）
    for k, v in (("title", a.title), ("memo", a.memo)):
        if v is not None:
            setattr(opts, k, v.replace("\\n", "\n"))
    for k, v in (("observer", a.observer), ("site", a.site), ("fontFamily", a.font),
                 ("stellariumDir", a.stellarium_dir)):
        if v is not None:
            setattr(opts, k, v)
    if a.dpi:
        opts.imageDpi = a.dpi
    if a.max_mb:
        opts.maxSizeMB = a.max_mb
    if a.no_compress:
        opts.autoCompress = False
    if a.focal:
        opts.focalOverride = a.focal
    if a.pixel:
        opts.pixelOverride = a.pixel
    if a.chart_fov:
        opts.chartFovMode, opts.chartFovDeg = "fixed", a.chart_fov
    if a.no_stellarium:
        opts.useStellarium = False
    opts.validate()


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    import sirilpy as s
    if not s.check_module_version(">=1.0.0"):
        print("%s: Siril 1.4.0 or later is required." % SCRIPT_NAME, file=sys.stderr)
        return 1
    siril = s.SirilInterface()
    try:
        siril.connect()
    except Exception as x:  # noqa: BLE001
        print("%s: cannot connect to Siril: %s" % (SCRIPT_NAME, x), file=sys.stderr)
        return 1

    def log(msg):
        try:
            siril.log(msg)
        except Exception:  # noqa: BLE001
            print(msg)

    headless = False
    try:
        headless = bool(siril.is_cli()) or (hasattr(siril, "is_headless") and bool(siril.is_headless()))
    except Exception:  # noqa: BLE001
        headless = os.getenv("SIRIL_HEADLESS") is not None
    batch = headless or bool(args.out)
    try:
        siril.cmd("requires", "1.4.0")
        s.ensure_installed("PyQt6", "astropy")
        if headless and not os.getenv("QT_QPA_PLATFORM") and sys.platform.startswith("linux") \
                and not os.getenv("DISPLAY") and not os.getenv("WAYLAND_DISPLAY"):
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QGuiApplication
        app = QGuiApplication.instance() or (QGuiApplication(sys.argv[:1]) if batch else QApplication(sys.argv[:1]))

        from sce import __version__
        from sce.exporter import Exporter, software_label
        from sce.strings import tr, set_lang

        try:
            settings_dir = siril.get_siril_userdatadir()
        except Exception:  # noqa: BLE001
            settings_dir = ""
        if not settings_dir:
            settings_dir = os.path.join(os.path.expanduser("~"), ".skychartexporter")
        settings_dir = os.path.join(settings_dir, "SkyChartExporter")
        try:
            data_dir = siril.get_siril_systemdatadir() or ""
        except Exception:  # noqa: BLE001
            data_dir = ""
        ex = Exporter(siril, log=log, settings_dir=settings_dir, siril_data_dir=data_dir, software=software_label())
        log("SkyChartExporter for Siril %s" % __version__)
        apply_args(ex.opts, args)
        set_lang(ex.opts.lang)
        try:
            ex.load_image()
        except Exception as x:  # noqa: BLE001
            msg = str(x) or tr("noSolution")
            log("SkyChartExporter: " + msg)
            if not batch:
                try:
                    siril.error_messagebox(msg, True)
                except Exception:  # noqa: BLE001
                    pass
            return 1

        if batch:
            # --out が無ければ、ダイアログで最後に保存したフォルダではなく画像と同じフォルダに書き出す
            out = os.path.abspath(os.path.expanduser(args.out)) if args.out else ex.default_output_path(False)
            r, model = ex.export_pdf(out)
            log("SKYCHART_OK %s size=%d dpi=%.0f px=%d objects=%d" % (out, r["size"], r["dpi"], r["photoPx"],
                                                                      len(model.list_items)))
            return 0

        from sce.dialog import SkyChartDialog
        while True:
            dlg = SkyChartDialog(ex)
            dlg.exec()
            ex.save_settings()
            if not dlg.restart:
                break
        del app
        return 0
    except Exception as x:  # noqa: BLE001
        log("SkyChartExporter: error: %s\n%s" % (x, traceback.format_exc()))
        if not batch:
            try:
                siril.error_messagebox("SkyChartExporter: %s" % x, True)
            except Exception:  # noqa: BLE001
                pass
        return 1
    finally:
        try:
            siril.disconnect()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
