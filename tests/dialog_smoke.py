# -*- coding: utf-8 -*-
"""ダイアログの起動確認（Siril なし・画面外描画）。FITS ファイルを Siril の画像の代わりに使う。

  .venv/bin/python tests/dialog_smoke.py 入力.fit 出力.png [ja|en]
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import numpy as np  # noqa: E402
from astropy.io import fits  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from sce import photo as P  # noqa: E402
from sce.catalog import Catalog  # noqa: E402
from sce.exporter import Exporter  # noqa: E402
from sce.imageinfo import AstropyTransform, ImageInfo, build_kw  # noqa: E402
from sce.render import numpy_to_qimage  # noqa: E402


class FakeImage:
    def __init__(self, path):
        with fits.open(path) as h:
            self.header = h[0].header
            data = h[0].data
        chw = P.as_chw(data)
        self.channels, self.height, self.width = chw.shape
        self.file_path = path
        self.transform = AstropyTransform(self.header, self.width, self.height)
        self.kw = build_kw(self.header)
        d = P.normalize(chw, 8 if self.header.get("BITPIX") == 8 else None)
        self._q = numpy_to_qimage(P.to_display_uint8(P.autostretch(d) if P.is_linear([float(np.median(c)) for c in d]) else d))

    def info(self):
        return ImageInfo(self.transform, self.width, self.height, self.kw, file_path=self.file_path, image_id="test")

    def photo(self, mode):
        return self._q


def main(path, out, lang="ja"):
    app = QApplication(sys.argv[:1])  # noqa: F841
    tmp = tempfile.mkdtemp()
    ex = Exporter(None, settings_dir=tmp, software="dialog smoke test")
    ex.image = FakeImage(path)
    ex.opts.lang = lang
    from sce.dialog import SkyChartDialog
    dlg = SkyChartDialog(ex)
    dlg.resize(1100, 900)
    dlg.show()
    app.processEvents()
    dlg.grab().save(out)
    # 操作: プリセット変更 → 個別変更でカスタムになる → 値の保存
    dlg._set_combo(dlg.preset_combo, "detailed")
    assert dlg.annot_checks["aConstBorders"].isChecked()
    dlg.annot_checks["aGrid"].setChecked(False)
    assert dlg.preset_combo.currentData() == "custom", dlg.preset_combo.currentData()
    dlg.title_edit.setText("テストタイトル")
    dlg.pixel_spin.setValue(3.76)
    dlg.store_values()
    assert ex.opts.title == "テストタイトル" and ex.opts.aGrid is False and ex.opts.annotPreset == "custom"
    img = ex.preview(1000)
    img.save(os.path.splitext(out)[0] + "-preview.png")
    r, model = ex.export_pdf(os.path.splitext(out)[0] + ".pdf")
    print("DIALOG_OK", r["size"], model.title, model.info_rows[5])
    dlg.done(0)
    assert os.path.isfile(os.path.join(tmp, "SkyChartExporter-settings.json"))


if __name__ == "__main__":
    main(*sys.argv[1:])
