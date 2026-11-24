# -*- coding: utf-8 -*-
"""設定値とその保存（Siril のユーザーデータフォルダに JSON で保存）、
アノテーションの書き込み量プリセット。PixInsight 版 SCE-Options.jsh の移植。"""

import json
import os

# 用紙サイズ（横向き, mm）
PAGE_SIZES = {
    "A4": {"w": 297.0, "h": 210.0, "ja": "A4 横", "en": "A4 landscape"},
    "A3": {"w": 420.0, "h": 297.0, "ja": "A3 横", "en": "A3 landscape"},
    "Letter": {"w": 279.4, "h": 215.9, "ja": "レター 横", "en": "Letter landscape"},
    "Wide": {"w": 297.0, "h": 167.06, "ja": "ワイド 16:9", "en": "Wide 16:9"},
}
PAGE_KEYS = ["A4", "A3", "Letter", "Wide"]

PRESET_KEYS = ["none", "minimal", "standard", "detailed", "all"]
PRESETS = {
    "none": dict(
        aMessier=False, aCaldwell=False, aNamed=False, aNGC=False, aNGCMag=12.0, aSNR=False,
        aBrightNeb=False, aDarkNeb=False, aPN=False, aClusters=False, aGalaxies=False, aGalaxyMag=14.0,
        aGalaxyClusters=False,
        aStars=False, aStarMag=4.0, aConstLines=False, aConstBorders=False, aConstNames=False,
        aGrid=False, aCompass=False),
    "minimal": dict(
        aMessier=True, aCaldwell=True, aNamed=True, aNGC=False, aNGCMag=10.0, aSNR=False,
        aBrightNeb=False, aDarkNeb=False, aPN=False, aClusters=False, aGalaxies=False, aGalaxyMag=12.0,
        aGalaxyClusters=False,
        aStars=True, aStarMag=2.5, aConstLines=False, aConstBorders=False, aConstNames=False,
        aGrid=False, aCompass=True),
    "standard": dict(
        aMessier=True, aCaldwell=True, aNamed=True, aNGC=True, aNGCMag=12.0, aSNR=True,
        aBrightNeb=True, aDarkNeb=False, aPN=False, aClusters=True, aGalaxies=False, aGalaxyMag=13.0,
        aGalaxyClusters=False,
        aStars=True, aStarMag=4.5, aConstLines=True, aConstBorders=False, aConstNames=False,
        aGrid=True, aCompass=True),
    "detailed": dict(
        aMessier=True, aCaldwell=True, aNamed=True, aNGC=True, aNGCMag=14.0, aSNR=True,
        aBrightNeb=True, aDarkNeb=True, aPN=True, aClusters=True, aGalaxies=True, aGalaxyMag=14.0,
        aGalaxyClusters=False,
        aStars=True, aStarMag=6.0, aConstLines=True, aConstBorders=True, aConstNames=True,
        aGrid=True, aCompass=True),
    "all": dict(
        aMessier=True, aCaldwell=True, aNamed=True, aNGC=True, aNGCMag=20.0, aSNR=True,
        aBrightNeb=True, aDarkNeb=True, aPN=True, aClusters=True, aGalaxies=True, aGalaxyMag=20.0,
        aGalaxyClusters=True,
        aStars=True, aStarMag=10.0, aConstLines=True, aConstBorders=True, aConstNames=True,
        aGrid=True, aCompass=True),
}

STRETCH_KEYS = ["auto", "none", "linked", "unlinked"]

# 保存しない項目（画像ごとに変わるもの）。画素サイズはカメラ名ごとに cameraPixelSizes へ記憶する
TRANSIENT_KEYS = ("title", "memo", "focalOverride", "pixelOverride")

SETTINGS_FILE = "SkyChartExporter-settings.json"


class Options:
    def __init__(self):
        self.set_defaults()

    def set_defaults(self):
        self.lang = "ja"
        self.title = ""
        self.observer = ""
        self.site = ""
        self.memo = ""
        self.pageSize = "A4"
        self.theme = "light"
        self.fontFamily = ""

        self.stretch = "auto"
        self.imageDpi = 200.0
        self.autoCompress = True
        self.maxSizeMB = 2.0
        self.focalOverride = 0.0
        self.pixelOverride = 0.0
        # カメラ名 → 画素サイズ（µm）。手入力した値をカメラごとに覚えておく
        self.cameraPixelSizes = {}

        self.useStellarium = True
        self.stellariumDir = ""

        self.annotPreset = "standard"
        self.apply_preset("standard")
        self.aLabelNames = True
        self.aLabelSize = 6.5

        self.chartFovMode = "auto"
        self.chartFovFactor = 8.0
        self.chartFovDeg = 30.0
        self.chartStarMag = 0.0
        self.chartDSO = True
        self.chartConstLines = True
        self.chartConstBorders = True
        self.chartConstNames = True
        self.chartGrid = True

        self.listMax = 40
        self.listStars = True

        self.outputDir = ""

    def apply_preset(self, name):
        p = PRESETS.get(name)
        if not p:
            return False
        for k, v in p.items():
            setattr(self, k, v)
        self.annotPreset = name
        return True

    def matching_preset(self):
        """現在の値に一致するプリセット名（なければ "custom"）"""
        for name in PRESET_KEYS:
            if all(getattr(self, k) == v for k, v in PRESETS[name].items()):
                return name
        return "custom"

    def to_dict(self, include_transient=False):
        return {k: v for k, v in vars(self).items() if include_transient or k not in TRANSIENT_KEYS}

    def update(self, d, include_transient=False):
        defaults = Options.__new__(Options)
        Options.set_defaults(defaults)
        for k, v in (d or {}).items():
            if not hasattr(defaults, k) or (k in TRANSIENT_KEYS and not include_transient):
                continue
            ref = getattr(defaults, k)
            # 型が合うものだけ受け付ける（数値は int/float を相互に許す）
            if isinstance(ref, bool):
                if isinstance(v, bool):
                    setattr(self, k, v)
            elif isinstance(ref, (int, float)):
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    setattr(self, k, type(ref)(v))
            elif isinstance(ref, type(v)):
                setattr(self, k, v)
        self.validate()

    def validate(self):
        if self.lang not in ("ja", "en"):
            self.lang = "ja"
        if self.pageSize not in PAGE_SIZES:
            self.pageSize = "A4"
        if self.theme not in ("light", "dark"):
            self.theme = "light"
        if self.stretch not in STRETCH_KEYS:
            self.stretch = "auto"
        if self.chartFovMode not in ("auto", "factor", "fixed"):
            self.chartFovMode = "auto"
        self.imageDpi = min(600.0, max(50.0, float(self.imageDpi)))
        self.maxSizeMB = min(100.0, max(0.2, float(self.maxSizeMB)))
        self.listMax = int(min(500, max(0, self.listMax)))
        if not isinstance(self.cameraPixelSizes, dict):
            self.cameraPixelSizes = {}

    # ------------------------------------------------------------------------

    def load(self, directory):
        path = os.path.join(directory, SETTINGS_FILE) if directory else ""
        if not path or not os.path.isfile(path):
            return False
        try:
            with open(path, encoding="utf-8") as f:
                self.update(json.load(f))
            return True
        except (OSError, ValueError) as x:
            print("SkyChartExporter: settings could not be loaded: %s" % x)
            return False

    def save(self, directory):
        if not directory:
            return False
        try:
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, SETTINGS_FILE)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
            return True
        except OSError as x:
            print("SkyChartExporter: settings could not be saved: %s" % x)
            return False
