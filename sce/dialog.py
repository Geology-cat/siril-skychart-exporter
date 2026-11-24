# -*- coding: utf-8 -*-
"""設定ダイアログ（PyQt6）。PixInsight 版 SCE-Dialog.jsh に相当する。"""

import os
import traceback

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
                             QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                             QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from .options import PAGE_KEYS, PAGE_SIZES, PRESET_KEYS, PRESETS
from .stellarium import find_stellarium_dir
from .strings import tr, fmt, set_lang
from .cameras import pixel_size_for_camera

# アノテーションの個別項目（キー, 文言キー, ツールチップの文言キー）
ANNOT_CHECKS = [
    ("aMessier", "aMessier", None), ("aCaldwell", "aCaldwell", None), ("aNamed", "aNamed", None),
    ("aNGC", "aNGC", None), ("aSNR", "aSNR", None), ("aBrightNeb", "aBrightNeb", "aBrightNebTip"),
    ("aDarkNeb", "aDarkNeb", "aDarkNebTip"), ("aPN", "aPN", "aPNTip"), ("aClusters", "aClusters", "aClustersTip"),
    ("aGalaxies", "aGalaxies", "aGalaxiesTip"), ("aGalaxyClusters", "aGalaxyClusters", "aGalaxyClustersTip"),
    ("aStars", "aStars", None), ("aConstLines", "aConstLines", None), ("aConstBorders", "aConstBorders", None),
    ("aConstNames", "aConstNames", None), ("aGrid", "aGrid", None), ("aCompass", "aCompass", None),
]
ANNOT_NUMS = [("aNGCMag", "aNGCMag", 4, 20), ("aGalaxyMag", "aGalaxyMag", 6, 20), ("aStarMag", "aStarMag", 0, 10)]


class PreviewDialog(QDialog):
    def __init__(self, image, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("previewTitle"))
        lab = QLabel()
        lab.setPixmap(QPixmap.fromImage(image))
        sc = QScrollArea()
        sc.setWidget(lab)
        sc.setWidgetResizable(False)
        lay = QVBoxLayout(self)
        lay.addWidget(sc)
        btn = QPushButton(tr("close"))
        btn.clicked.connect(self.accept)
        lay.addWidget(btn, alignment=Qt.AlignmentFlag.AlignRight)
        scr = QApplication.primaryScreen()
        avail = scr.availableGeometry() if scr else None
        w = min(image.width() + 40, avail.width() - 60 if avail else 1500)
        h = min(image.height() + 80, avail.height() - 80 if avail else 1000)
        self.resize(w, h)


class SkyChartDialog(QDialog):
    def __init__(self, exporter, parent=None):
        super().__init__(parent)
        self.ex = exporter
        self.opts = exporter.opts
        self.restart = False
        self._updating = False
        set_lang(self.opts.lang)
        self.setWindowTitle(tr("dialogTitle"))
        self.build()
        self.load_values()

    # ------------------------------------------------------------------------

    def build(self):
        o = self.opts
        root = QVBoxLayout(self)
        top = QHBoxLayout()
        help_ = QLabel(tr("help"))
        help_.setWordWrap(True)
        top.addWidget(help_, 1)
        top.addWidget(QLabel(tr("language")))
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["日本語", "English"])
        self.lang_combo.setCurrentIndex(1 if o.lang == "en" else 0)
        self.lang_combo.currentIndexChanged.connect(self.on_lang)
        top.addWidget(self.lang_combo)
        root.addLayout(top)

        body = QWidget()
        cols = QHBoxLayout(body)
        left = QVBoxLayout()
        right = QVBoxLayout()
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # --- 対象画像 ---
        g = QGroupBox(tr("targetImage"))
        v = QVBoxLayout(g)
        img = self.ex.image
        name = os.path.basename(img.file_path) if img.file_path else "(unsaved)"
        v.addWidget(QLabel("%s  —  %d × %d px, %d ch" % (name, img.width, img.height, img.channels)))
        self.image_info = QLabel()
        self.image_info.setWordWrap(True)
        v.addWidget(self.image_info)
        left.addWidget(g)

        # --- ドキュメント ---
        g = QGroupBox(tr("grpDocument"))
        f = QFormLayout(g)
        self.title_edit = QLineEdit()
        self.title_edit.setToolTip(tr("titleTip"))
        self.title_edit.setPlaceholderText(tr("titleTip"))
        f.addRow(tr("title"), self.title_edit)
        self.observer_edit = QLineEdit()
        f.addRow(tr("observer"), self.observer_edit)
        self.site_edit = QLineEdit()
        f.addRow(tr("site"), self.site_edit)
        self.page_combo = QComboBox()
        for k in PAGE_KEYS:
            self.page_combo.addItem(PAGE_SIZES[k]["en" if o.lang == "en" else "ja"], k)
        f.addRow(tr("pageSize"), self.page_combo)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem(tr("themeLight"), "light")
        self.theme_combo.addItem(tr("themeDark"), "dark")
        f.addRow(tr("theme"), self.theme_combo)
        self.font_edit = QLineEdit()
        self.font_edit.setPlaceholderText(tr("fontAuto"))
        self.font_edit.setToolTip(tr("fontTip"))
        f.addRow(tr("font"), self.font_edit)
        left.addWidget(g)

        # --- 画像と圧縮 ---
        g = QGroupBox(tr("grpImage"))
        f = QFormLayout(g)
        self.stretch_combo = QComboBox()
        for k, label in (("auto", "stretchAuto"), ("none", "stretchNone"), ("linked", "stretchSTF"),
                         ("unlinked", "stretchAutoSTF")):
            self.stretch_combo.addItem(tr(label), k)
        f.addRow(tr("stretch"), self.stretch_combo)
        self.dpi_spin = self._dspin(50, 600, 0, 10)
        f.addRow(tr("imageDpi"), self.dpi_spin)
        self.compress_check = QCheckBox(tr("autoCompress"))
        f.addRow(self.compress_check)
        self.maxsize_spin = self._dspin(0.2, 100, 1, 0.5)
        f.addRow(tr("maxSize"), self.maxsize_spin)
        self.focal_spin = self._dspin(0, 20000, 0, 1)
        f.addRow(tr("focalOverride"), self.focal_spin)
        self.pixel_spin = self._dspin(0, 30, 2, 0.01)
        f.addRow(tr("pixelOverride"), self.pixel_spin)
        self.focal_spin.valueChanged.connect(self.update_image_info)
        self.pixel_spin.valueChanged.connect(self.update_image_info)
        left.addWidget(g)

        # --- メモ ---
        g = QGroupBox(tr("grpMemo"))
        v = QVBoxLayout(g)
        self.memo_edit = QPlainTextEdit()
        self.memo_edit.setMinimumHeight(70)
        v.addWidget(self.memo_edit)
        left.addWidget(g)

        # --- カタログ ---
        g = QGroupBox(tr("grpCatalog"))
        v = QVBoxLayout(g)
        self.stel_check = QCheckBox(tr("useStellarium"))
        v.addWidget(self.stel_check)
        h = QHBoxLayout()
        h.addWidget(QLabel(tr("stellariumDir")))
        self.stel_edit = QLineEdit()
        self.stel_edit.setToolTip(tr("stellariumDirTip"))
        self.stel_edit.setPlaceholderText(tr("stellariumDirTip"))
        h.addWidget(self.stel_edit, 1)
        b = QPushButton(tr("browse"))
        b.clicked.connect(self.browse_stellarium)
        h.addWidget(b)
        v.addLayout(h)
        self.stel_status = QLabel()
        self.stel_status.setWordWrap(True)
        v.addWidget(self.stel_status)
        note = QLabel(tr("chartStarsNote"))
        note.setWordWrap(True)
        v.addWidget(note)
        self.stel_edit.editingFinished.connect(self.update_stel_status)
        self.stel_check.toggled.connect(self.update_stel_status)
        left.addWidget(g)
        left.addStretch(1)

        # --- アノテーション ---
        g = QGroupBox(tr("grpAnnot"))
        v = QVBoxLayout(g)
        h = QHBoxLayout()
        h.addWidget(QLabel(tr("annotLevel")))
        self.preset_combo = QComboBox()
        for k, label in zip(PRESET_KEYS, ("lvNone", "lvMinimal", "lvStandard", "lvDetailed", "lvAll")):
            self.preset_combo.addItem(tr(label), k)
        self.preset_combo.addItem(tr("lvCustom"), "custom")
        self.preset_combo.currentIndexChanged.connect(self.on_preset)
        h.addWidget(self.preset_combo, 1)
        v.addLayout(h)
        grid = QGridLayout()
        self.annot_checks = {}
        for i, (k, label, tip) in enumerate(ANNOT_CHECKS):
            cb = QCheckBox(tr(label))
            if tip:
                cb.setToolTip(tr(tip))
            cb.toggled.connect(self.on_annot_changed)
            self.annot_checks[k] = cb
            grid.addWidget(cb, i // 2, i % 2)
        v.addLayout(grid)
        f = QFormLayout()
        self.annot_nums = {}
        for k, label, lo, hi in ANNOT_NUMS:
            sp = self._dspin(lo, hi, 1, 0.5)
            sp.valueChanged.connect(self.on_annot_changed)
            self.annot_nums[k] = sp
            f.addRow(tr(label), sp)
        self.label_names_check = QCheckBox(tr("aLabelNames"))
        f.addRow(self.label_names_check)
        self.label_size_spin = self._dspin(4, 14, 1, 0.5)
        f.addRow(tr("aLabelSize"), self.label_size_spin)
        v.addLayout(f)
        n = QLabel(tr("catNeedsStel"))
        n.setWordWrap(True)
        v.addWidget(n)
        right.addWidget(g)

        # --- 星図 ---
        g = QGroupBox(tr("grpChart"))
        f = QFormLayout(g)
        self.fov_combo = QComboBox()
        for k, label in (("auto", "fovAuto"), ("factor", "fovFactor"), ("fixed", "fovFixed")):
            self.fov_combo.addItem(tr(label), k)
        f.addRow(tr("chartFov"), self.fov_combo)
        self.fov_factor_spin = self._dspin(1.2, 100, 1, 0.5)
        f.addRow(tr("chartFactor"), self.fov_factor_spin)
        self.fov_deg_spin = self._dspin(0.5, 150, 1, 1)
        f.addRow(tr("chartDeg"), self.fov_deg_spin)
        self.fov_combo.currentIndexChanged.connect(self.update_fov_enabled)
        self.chart_mag_spin = self._dspin(0, 10, 1, 0.5)
        f.addRow(tr("chartStarMag"), self.chart_mag_spin)
        self.chart_checks = {}
        for k, label in (("chartDSO", "cDSO"), ("chartConstLines", "cConstLines"),
                         ("chartConstBorders", "cConstBorders"), ("chartConstNames", "cConstNames"),
                         ("chartGrid", "cGrid")):
            cb = QCheckBox(tr(label))
            self.chart_checks[k] = cb
            f.addRow(cb)
        right.addWidget(g)

        # --- 天体一覧 ---
        g = QGroupBox(tr("grpList"))
        f = QFormLayout(g)
        self.list_max_spin = QSpinBox()
        self.list_max_spin.setRange(0, 500)
        f.addRow(tr("listMax"), self.list_max_spin)
        self.list_stars_check = QCheckBox(tr("listStars"))
        f.addRow(self.list_stars_check)
        right.addWidget(g)
        right.addStretch(1)

        # --- ボタン ---
        btns = QHBoxLayout()
        btns.addStretch(1)
        b = QPushButton(tr("preview"))
        b.clicked.connect(self.do_preview)
        btns.addWidget(b)
        b = QPushButton(tr("exportPdf"))
        b.setDefault(True)
        b.clicked.connect(self.do_export)
        btns.addWidget(b)
        b = QPushButton(tr("close"))
        b.clicked.connect(self.reject)
        btns.addWidget(b)
        root.addLayout(btns)

        scr = QApplication.primaryScreen()
        if scr:
            a = scr.availableGeometry()
            self.resize(min(1100, a.width() - 80), min(860, a.height() - 80))

    @staticmethod
    def _dspin(lo, hi, decimals, step):
        sp = QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setDecimals(decimals)
        sp.setSingleStep(step)
        return sp

    @staticmethod
    def _set_combo(combo, data):
        i = combo.findData(data)
        if i >= 0:
            combo.setCurrentIndex(i)

    # ------------------------------------------------------------------------

    def load_values(self):
        o = self.opts
        self._updating = True
        self.title_edit.setText(o.title)
        self.observer_edit.setText(o.observer)
        self.site_edit.setText(o.site)
        self._set_combo(self.page_combo, o.pageSize)
        self._set_combo(self.theme_combo, o.theme)
        self.font_edit.setText(o.fontFamily)
        self._set_combo(self.stretch_combo, o.stretch)
        self.dpi_spin.setValue(o.imageDpi)
        self.compress_check.setChecked(o.autoCompress)
        self.maxsize_spin.setValue(o.maxSizeMB)
        self.focal_spin.setValue(o.focalOverride)
        self.pixel_spin.setValue(o.pixelOverride)
        self.memo_edit.setPlainText(o.memo)
        self.stel_check.setChecked(o.useStellarium)
        self.stel_edit.setText(o.stellariumDir)
        for k, cb in self.annot_checks.items():
            cb.setChecked(getattr(o, k))
        for k, sp in self.annot_nums.items():
            sp.setValue(getattr(o, k))
        self.label_names_check.setChecked(o.aLabelNames)
        self.label_size_spin.setValue(o.aLabelSize)
        self._set_combo(self.fov_combo, o.chartFovMode)
        self.fov_factor_spin.setValue(o.chartFovFactor)
        self.fov_deg_spin.setValue(o.chartFovDeg)
        self.chart_mag_spin.setValue(o.chartStarMag)
        for k, cb in self.chart_checks.items():
            cb.setChecked(getattr(o, k))
        self.list_max_spin.setValue(o.listMax)
        self.list_stars_check.setChecked(o.listStars)
        self._set_combo(self.preset_combo, o.matching_preset())
        self._updating = False
        self.update_stel_status()
        self.update_image_info()
        self.update_fov_enabled()

    def update_fov_enabled(self):
        mode = self.fov_combo.currentData()
        self.fov_factor_spin.setEnabled(mode == "factor")
        self.fov_deg_spin.setEnabled(mode == "fixed")

    def store_values(self):
        o = self.opts
        o.title = self.title_edit.text().strip()
        o.observer = self.observer_edit.text().strip()
        o.site = self.site_edit.text().strip()
        o.pageSize = self.page_combo.currentData()
        o.theme = self.theme_combo.currentData()
        o.fontFamily = self.font_edit.text().strip()
        o.stretch = self.stretch_combo.currentData()
        o.imageDpi = self.dpi_spin.value()
        o.autoCompress = self.compress_check.isChecked()
        o.maxSizeMB = self.maxsize_spin.value()
        o.focalOverride = self.focal_spin.value()
        o.pixelOverride = self.pixel_spin.value()
        o.memo = self.memo_edit.toPlainText()
        o.useStellarium = self.stel_check.isChecked()
        o.stellariumDir = self.stel_edit.text().strip()
        for k, cb in self.annot_checks.items():
            setattr(o, k, cb.isChecked())
        for k, sp in self.annot_nums.items():
            setattr(o, k, sp.value())
        o.aLabelNames = self.label_names_check.isChecked()
        o.aLabelSize = self.label_size_spin.value()
        o.chartFovMode = self.fov_combo.currentData()
        o.chartFovFactor = self.fov_factor_spin.value()
        o.chartFovDeg = self.fov_deg_spin.value()
        o.chartStarMag = self.chart_mag_spin.value()
        for k, cb in self.chart_checks.items():
            setattr(o, k, cb.isChecked())
        o.listMax = self.list_max_spin.value()
        o.listStars = self.list_stars_check.isChecked()
        o.annotPreset = o.matching_preset()
        o.validate()

    # ------------------------------------------------------------------------

    def on_lang(self, index):
        self.store_values()
        self.opts.lang = "en" if index == 1 else "ja"
        self.restart = True
        self.accept()

    def on_preset(self):
        if self._updating:
            return
        key = self.preset_combo.currentData()
        if key not in PRESETS:
            return
        self._updating = True
        for k, v in PRESETS[key].items():
            if k in self.annot_checks:
                self.annot_checks[k].setChecked(v)
            elif k in self.annot_nums:
                self.annot_nums[k].setValue(v)
        self._updating = False

    def on_annot_changed(self):
        if self._updating:
            return
        vals = {k: cb.isChecked() for k, cb in self.annot_checks.items()}
        vals.update({k: sp.value() for k, sp in self.annot_nums.items()})
        match = "custom"
        for name in PRESET_KEYS:
            if all(vals.get(k) == v for k, v in PRESETS[name].items()):
                match = name
                break
        self._updating = True
        self._set_combo(self.preset_combo, match)
        self._updating = False

    def update_stel_status(self):
        if not self.stel_check.isChecked():
            self.stel_status.setText("")
            return
        d = find_stellarium_dir(self.stel_edit.text().strip())
        self.stel_status.setText(fmt(tr("stelFound"), d) if d else tr("stelNotFound"))

    def browse_stellarium(self):
        d = QFileDialog.getExistingDirectory(self, tr("stellariumDir"), self.stel_edit.text() or "/Applications")
        if d:
            self.stel_edit.setText(d)
            self.update_stel_status()

    def update_image_info(self):
        """検出したカメラ名・画素サイズ（とその出どころ）・焦点距離を表示する"""
        try:
            info = self.ex.image.info()
        except Exception as x:  # noqa: BLE001
            self.image_info.setText(tr("noSolutionShort") + ": %s" % x)
            return
        o = self.opts
        parts = [fmt(tr("infoScale"), info.scale_arcsec)]
        cam = info.camera
        parts.append(fmt(tr("infoCamera"), cam) if cam else tr("infoNoCamera"))
        px, src = 0.0, ""
        if self.pixel_spin.value() > 0:
            px, src = self.pixel_spin.value(), tr("srcManual")
        elif info.pixel_size:
            px, src = info.pixel_size, tr("srcHeader")
        elif cam and o.cameraPixelSizes.get(cam, 0) > 0:
            px, src = o.cameraPixelSizes[cam], tr("srcRemembered")
        elif cam and pixel_size_for_camera(cam) > 0:
            px, src = pixel_size_for_camera(cam), tr("srcCamera")
        parts.append(fmt(tr("infoPixel"), ("%.2f" % px).rstrip("0").rstrip("."), src) if px > 0 else tr("infoNoPixel"))

        class _O:
            pass
        tmp = _O()
        tmp.pixelOverride = self.pixel_spin.value()
        tmp.focalOverride = self.focal_spin.value()
        tmp.cameraPixelSizes = o.cameraPixelSizes
        f = info.resolve_focal(tmp)
        if f["value"]:
            parts.append(fmt(tr("infoFocalApprox"), f["value"]) if f["source"] == "fov35" else
                         fmt(tr("infoFocal"), f["value"]))
        self.image_info.setText("\n".join(parts))

    # ------------------------------------------------------------------------

    def _busy(self, on):
        if on:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        else:
            QApplication.restoreOverrideCursor()
        QApplication.processEvents()

    def _error(self, title, x):
        self.ex.log("SkyChartExporter: %s: %s\n%s" % (title, x, traceback.format_exc()))
        QMessageBox.critical(self, "SkyChartExporter", "%s\n\n%s" % (title, x))

    def do_preview(self):
        self.store_values()
        self._busy(True)
        try:
            scr = QApplication.primaryScreen()
            width = 1400
            if scr:
                width = max(900, min(1800, scr.availableGeometry().width() - 120))
            img = self.ex.preview(width)
        except Exception as x:  # noqa: BLE001
            self._busy(False)
            self._error(tr("exportFailed"), x)
            return
        self._busy(False)
        PreviewDialog(img, self).exec()

    def do_export(self):
        self.store_values()
        default = self.ex.default_output_path()
        path, _ = QFileDialog.getSaveFileName(self, tr("saveTitle"), default, "PDF (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        self.opts.outputDir = os.path.dirname(path)
        self._busy(True)
        try:
            r, model = self.ex.export_pdf(path)
        except Exception as x:  # noqa: BLE001
            self._busy(False)
            self._error(tr("exportFailed"), x)
            return
        self._busy(False)
        self.ex.save_settings()
        msg = "%s\n%s\n\n%.2f MB · %d px · %.0f dpi" % (tr("exported"), path, r["size"] / 1048576, r["photoPx"], r["dpi"])
        if r.get("overTarget"):
            msg += "\n\n" + tr("sizeOver")
        QMessageBox.information(self, "SkyChartExporter", msg)

    def done(self, result):
        self.store_values()
        self.ex.save_settings()
        super().done(result)
