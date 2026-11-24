# -*- coding: utf-8 -*-
"""
カメラ名 → 画素サイズ（µm）の表（PixInsight 版 SCE-Cameras.jsh から移植）。

プレートソルブで分かるのは「1画素あたりの角度（″/px）」だけで、焦点距離は
    焦点距離[mm] = 206.265 × 画素サイズ[µm] ÷ 画素スケール[″/px]
なので画素サイズが必要になる。画像に画素サイズの記録が無いときは、
FITS の INSTRUME などに記録されたカメラ名からこの表で画素サイズを推定する。
一致判定は英数字だけに正規化した大文字の名前に対して行う（上から順に評価）。
"""

import re

CAMERAS = [
    ('ASI2600', 3.76),
    ('ASI6200', 3.76),
    ('ASI2400', 5.94),
    ('ASI533', 3.76),
    ('ASI571', 3.76),
    ('ASI294', 4.63),
    ('ASI1600', 3.8),
    ('ASI183', 2.4),
    ('ASI071', 4.78),
    ('ASI094', 4.88),
    ('ASI128', 5.97),
    ('ASI585', 2.9),
    ('ASI678', 2),
    ('ASI676', 2),
    ('ASI662', 2.9),
    ('ASI664', 2.9),
    ('ASI462', 2.9),
    ('ASI485', 2.9),
    ('ASI482', 5.8),
    ('ASI715', 1.45),
    ('ASI224', 3.75),
    ('ASI120', 3.75),
    ('ASI174', 5.86),
    ('ASI178', 2.4),
    ('ASI290', 2.9),
    ('ASI385', 3.75),
    ('ASI432', 9),
    ('ASI220', 4),
    ('QHY268', 3.76),
    ('QHY600', 3.76),
    ('QHY533', 3.76),
    ('QHY294', 4.63),
    ('QHY410', 5.94),
    ('QHY183', 2.4),
    ('QHY163', 3.8),
    ('QHY168', 4.78),
    ('QHY367', 4.88),
    ('QHY128', 5.97),
    ('QHY247', 3.91),
    ('QHY16200', 6),
    ('QHY9', 5.4),
    ('QHY5III462|QHY5III585', 2.9),
    ('QHY5III678', 2),
    ('QHY5III715', 1.45),
    ('QHY5III174', 5.86),
    ('QHY5III178', 2.4),
    ('QHY5III290', 2.9),
    ('QHY5III224', 3.75),
    ('QHY5LII', 3.75),
    ('POSEIDON', 3.76),
    ('ARES', 3.76),
    ('ZEUS', 3.76),
    ('URANUS', 2.9),
    ('C326000|C361000', 3.76),
    ('G28300|STF8300|ST8300|STT8300|KAF8300|383L', 5.4),
    ('16200', 6),
    ('STL11000|KAI11000|STXL6303|KAF16803', 9),
    ('460EX|428EX', 4.54),
    ('414EX', 6.45),
    ('ATIKHORIZON', 3.8),
    ('IMX571|IMX455|IMX533|IMX541', 3.76),
    ('IMX294', 4.63),
    ('IMX410', 5.94),
    ('IMX585|IMX462|IMX290|IMX464', 2.9),
    ('IMX678|IMX676', 2),
    ('IMX183|IMX178', 2.4),
    ('IMX174', 5.86),
    ('IMX224', 3.75),
    ('EOSRA?$', 5.36),
    ('6DMARKII|6DMKII|6D2', 5.67),
    ('EOS6D', 6.55),
    ('5DMARKIV|5DMKIV|5D4', 5.36),
    ('5DMARKIII|5DMKIII|5D3', 6.25),
    ('5DMARKII|5DMKII|5D2', 6.41),
    ('5DS', 4.14),
    ('EOSR5', 4.39),
    ('EOSR6MARKII|EOSR6MKII|EOSR6II|EOSR6M2', 5.98),
    ('EOSR6', 6.56),
    ('EOSR8', 5.98),
    ('EOSRP', 5.75),
    ('EOSR7', 3.2),
    ('EOSR10', 3.72),
    ('EOS90D', 3.2),
    ('EOS80D|EOS77D|EOS800D|EOS850D|KISSX9I|KISSX10I', 3.72),
    ('7DMARKII|7DMKII', 4.1),
    ('EOS70D', 4.1),
    ('EOS60D|EOS7D|EOS600D|EOS650D|EOS700D|KISSX5|KISSX6I|KISSX7I', 4.3),
    ('ILCE7SM|ILCE7S$|A7S', 8.4),
    ('ILCE7RM4|ILCE7RM5|A7RIV|A7RV', 3.76),
    ('ILCE7RM2|ILCE7RM3|A7RIII|A7RII', 4.51),
    ('ILCE7M4|A7IV', 5.12),
    ('ILCE7M3|ILCE7C|A7III', 5.93),
    ('ILCE6000|ILCE6400|ILCE6600', 3.89),
    ('XT5|XH2$|XH2[^S]', 3.04),
    ('XT3|XT4|XT30|XS10|XH2S', 3.76),
    ('GFX100', 3.76),
    ('GFX50', 5.3),
    ('PENTAXK1', 4.88),
    ('EM1MARKII|EM1MARKIII|EM5MARKIII|OM1', 3.32),
]

# Nikon は番号の重なりが多いので個別に判定する
NIKON = [
    ("D850|Z7|Z8|Z9", 4.35), ("D810|D800", 4.88), ("D750|D780|D610|D600|Z5|ZF", 5.95), ("Z6", 5.94),
    ("D5300|D5500|D5600", 3.89), ("D7500", 4.2), ("D7200", 3.92),
]

_CAMERAS_RE = [(re.compile(p), v) for p, v in CAMERAS]
_NIKON_RE = [(re.compile(p), v) for p, v in NIKON]


def pixel_size_for_camera(name):
    """カメラ名から画素サイズ（µm）を推定する。分からなければ 0。"""
    if not name:
        return 0.0
    n = re.sub(r"[^A-Z0-9]", "", str(name).upper())
    if not n:
        return 0.0
    if "NIKON" in n or re.match(r"^D\d{3,4}", n):
        for rx, v in _NIKON_RE:
            if rx.search(n):
                return v
    for rx, v in _CAMERAS_RE:
        if v > 0 and rx.search(n):
            return v
    return 0.0
