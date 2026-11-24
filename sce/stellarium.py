# -*- coding: utf-8 -*-
"""インストール済みの Stellarium から星雲・星団・銀河のカタログと星座境界を読み込む。

  nebulae/default/catalog.dat        … DSO カタログ本体（gzip + Qt QDataStream）
  nebulae/default/names.dat          … 通称（英語）
  translations/stellarium-sky/ja.qm  … 通称の日本語訳（Qt の翻訳ファイル）
  skycultures/modern/index.json      … 星座境界（IAU, B1875）

データは利用者の Stellarium から実行時に読むだけで、再配布はしない。
PixInsight 版 SCE-Stellarium.jsh の移植（gzip の展開は Python 標準の gzip を使う）。
"""

import gzip
import json
import os
import re
import struct
import sys

from .astro import norm_ra, R2D

# catalog.dat の各レコードにあるカタログ番号の並び（Stellarium 3.x 形式）。
# True: 文字列型（QString）、False: 32 ビット整数。
STEL_IDS = [
    ("NGC", False), ("IC", False), ("M", False), ("C", False), ("B", False), ("Sh2", False), ("VdB", False),
    ("RCW", False), ("LDN", False), ("LBN", False), ("Cr", False), ("Mel", False), ("PGC", False), ("UGC", False),
    ("Ced", True), ("Arp", False), ("VV", False), ("PK", True), ("PNG", True), ("SNRG", True), ("ACO", True),
    ("HCG", True), ("ESO", True), ("VdBH", True), ("DWB", False), ("Tr", False), ("St", False), ("Ru", False),
    ("VdBHa", False),
]

# 表示用の書式と、主表記に使う優先順位
STEL_FORMAT = {
    "M": "M%s", "NGC": "NGC %s", "IC": "IC %s", "Sh2": "Sh2-%s", "C": "C%s", "B": "B%s", "VdB": "vdB %s",
    "LBN": "LBN %s", "LDN": "LDN %s", "RCW": "RCW %s", "Ced": "Ced %s", "Cr": "Cr %s", "Mel": "Mel %s",
    "Tr": "Tr %s", "St": "St %s", "Ru": "Ru %s", "VdBHa": "vdB-Ha %s", "DWB": "DWB %s", "VdBH": "vdBH %s",
    "Arp": "Arp %s", "VV": "VV %s", "HCG": "HCG %s", "ACO": "Abell %s", "PK": "PK %s", "PNG": "PN G%s",
    "SNRG": "SNR G%s", "UGC": "UGC %s", "ESO": "ESO %s", "PGC": "PGC %s",
}
STEL_PRIORITY = ["M", "NGC", "IC", "Sh2", "C", "B", "VdB", "LBN", "LDN", "RCW", "Ced", "Cr", "Mel", "Tr", "St",
                 "Ru", "VdBHa", "DWB", "VdBH", "Arp", "VV", "HCG", "ACO", "PK", "PNG", "SNRG", "UGC", "PGC", "ESO"]

# names.dat の接頭辞 → カタログキー
STEL_PREFIX = {
    "M": "M", "NGC": "NGC", "IC": "IC", "C": "C", "B": "B", "SH2": "Sh2", "VDB": "VdB", "RCW": "RCW", "LDN": "LDN",
    "LBN": "LBN", "CR": "Cr", "MEL": "Mel", "PGC": "PGC", "UGC": "UGC", "CED": "Ced", "ARP": "Arp", "VV": "VV",
    "PK": "PK", "PNG": "PNG", "SNRG": "SNRG", "ACO": "ACO", "HCG": "HCG", "ESO": "ESO", "VDBH": "VdBH", "DWB": "DWB",
    "TR": "Tr", "ST": "St", "RU": "Ru", "VDBHA": "VdBHa",
}

_NEBULA_TYPES = {"nebula", "emissionNebula", "hii", "reflectionNebula", "darkNebula", "molecularCloud", "snr",
                 "clusterNebula"}


def stel_type_key(t):
    """Stellarium の天体種別番号 → 種別キー"""
    if t in (0, 1, 2, 21, 22):
        return "galaxy"
    return {
        3: "interactingGalaxy", 4: "quasar", 25: "quasar", 5: "starCluster", 6: "openCluster",
        7: "globularCluster", 8: "association", 9: "starCloud", 10: "nebula", 14: "nebula", 19: "nebula",
        20: "nebula", 11: "planetaryNebula", 26: "planetaryNebula", 27: "planetaryNebula", 12: "darkNebula",
        13: "reflectionNebula", 15: "emissionNebula", 16: "clusterNebula", 17: "hii", 18: "snr", 32: "snr",
        23: "molecularCloud", 24: "star", 28: "star", 29: "star", 30: "star", 33: "galaxyCluster", 34: "hii",
    }.get(t, "other")


def find_stellarium_dir(preferred=""):
    """Stellarium のデータフォルダ（nebulae/default がある場所）を探す。見つからなければ ""。"""
    home = os.path.expanduser("~")
    cands = []
    if preferred:
        cands.append(preferred)
    cands += [
        "/Applications/Stellarium.app/Contents/Resources",
        os.path.join(home, "Applications/Stellarium.app/Contents/Resources"),
        "C:/Program Files/Stellarium",
        "C:/Program Files (x86)/Stellarium",
        "/usr/share/stellarium",
        "/usr/local/share/stellarium",
        "/snap/stellarium-daily/current/usr/share/stellarium",
        "/var/lib/flatpak/app/org.stellarium.Stellarium/current/active/files/share/stellarium",
        os.path.join(home, ".local/share/flatpak/app/org.stellarium.Stellarium/current/active/files/share/stellarium"),
    ]
    for c in cands:
        d = c.rstrip("/\\")
        # .app を指定された場合も受け付ける
        if d.lower().endswith(".app"):
            d += "/Contents/Resources"
        if os.path.isfile(os.path.join(d, "nebulae", "default", "catalog.dat")):
            return d
    return ""


def read_qm(path):
    """Qt の翻訳ファイル（.qm）から 原文 → 訳文 の辞書を作る"""
    result = {}
    if not os.path.isfile(path):
        return result
    with open(path, "rb") as f:
        b = f.read()
    o = 16
    n_total = len(b)
    while o + 5 <= n_total:
        tag = b[o]
        (length,) = struct.unpack_from(">I", b, o + 1)
        start = o + 5
        o = start + length
        if tag != 0x69:  # メッセージ以外のセクションは不要
            continue
        p, end = start, min(start + length, n_total)
        src = tr = None
        while p < end:
            t = b[p]
            p += 1
            if t == 1:
                if src and tr and tr != src and src not in result:
                    result[src] = tr
                src = tr = None
                continue
            if t == 5:
                p += 4
                continue
            (n,) = struct.unpack_from(">I", b, p)
            p += 4
            if n == 0xFFFFFFFF:
                continue
            if t == 3:
                tr = b[p:p + n].decode("utf-16-be", errors="replace")
            elif t == 6:
                src = b[p:p + n].decode("utf-8", errors="replace")
            p += n
    return result


def norm_id(s):
    s = str(s).strip()
    return str(int(s)) if s.isdigit() else s.upper()


def read_names(path):
    """names.dat → { "NGC:7000": ["North America Nebula", ...] }"""
    result = {}
    if not os.path.isfile(path):
        return result
    rx = re.compile(r'_\("(.*?)"\)')
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if len(line) < 21 or line.startswith("#"):
                continue
            prefix = line[0:5].strip().upper()
            ident = line[5:20].strip()
            m = rx.search(line[20:])
            cat = STEL_PREFIX.get(prefix)
            if not cat or not m or not ident:
                continue
            result.setdefault(cat + ":" + norm_id(ident), []).append(m.group(1))
    return result


class StellariumFormatError(Exception):
    pass


def load_dso(directory, log=None):
    """catalog.dat を読み、天体レコード（dict）のリストと日本語訳の辞書を返す"""
    with open(os.path.join(directory, "nebulae", "default", "catalog.dat"), "rb") as f:
        raw = f.read()
    d = gzip.decompress(raw)
    n_total = len(d)
    pos = [0]

    def qstr():
        (n,) = struct.unpack_from(">I", d, pos[0])
        pos[0] += 4
        if n == 0xFFFFFFFF or n == 0:
            return ""
        s = d[pos[0]:pos[0] + n].decode("utf-16-be", errors="replace")
        pos[0] += n
        return s

    version = qstr()
    qstr()  # edition
    if not version.startswith("3.") and log:
        log("Stellarium catalog version %s (expected 3.x)" % version)

    names = read_names(os.path.join(directory, "nebulae", "default", "names.dat"))
    ja = read_qm(os.path.join(directory, "translations", "stellarium-sky", "ja.qm"))

    rec_head = struct.Struct(">I4dI")
    shape = struct.Struct(">ddi")
    out = []
    seq = 0
    while pos[0] < n_total:
        seq += 1
        o = pos[0]
        # 通し番号が 1, 2, 3… と続かなければ形式が想定と違う（誤読を防ぐ）
        if o + 108 > n_total:
            raise StellariumFormatError("Unsupported Stellarium catalog format (record %d)" % seq)
        rid, ra, dec, bmag, vmag, otype = rec_head.unpack_from(d, o)
        if rid != seq:
            raise StellariumFormatError("Unsupported Stellarium catalog format (record %d)" % seq)
        pos[0] = o + 40
        qstr()  # 形態分類
        maj, minr, pa = shape.unpack_from(d, pos[0])
        pos[0] += 20 + 48  # 赤方偏移・視差・距離（不要）
        ids = {}
        for key, is_str in STEL_IDS:
            if is_str:
                s = qstr().strip()
                if s:
                    ids[key] = s
            else:
                (v,) = struct.unpack_from(">I", d, pos[0])
                pos[0] += 4
                if v != 0:
                    ids[key] = str(v)
        primary = next((k for k in STEL_PRIORITY if k in ids), None)
        if primary is None:
            continue

        mag = vmag if vmag < 98 else (bmag if bmag < 98 else None)
        tk = stel_type_key(otype)
        # LDN・LBN などの「等級」欄は不透明度や明るさの階級なので、
        # M/NGC/IC/C 番号を持たない星雲類では等級として扱わない
        if not any(k in ids for k in ("M", "NGC", "IC", "C")) and tk in _NEBULA_TYPES:
            mag = None
        rec = {
            "key": primary + ":" + ids[primary],
            "ids": ids,
            "m": "M" + ids["M"] if "M" in ids else None,
            "c": "C" + ids["C"] if "C" in ids else None,
            "ngc": ("NGC" + ids["NGC"]) if "NGC" in ids else (("IC" + ids["IC"]) if "IC" in ids else None),
            "ra": norm_ra(ra * R2D),
            "dec": dec * R2D,
            "mag": mag,
            "diam": maj * 60 if maj > 0 else None,
            "axisRatio": maj / minr if (maj > 0 and minr > 0) else None,
            "pa": pa if (maj > 0 and 0 < minr < maj) else None,
            "type": tk,
            "en": "",
            "ja": "",
            "jaStel": "",
            "isSNR": ("SNRG" in ids) or otype in (18, 32),
            "desig": STEL_FORMAT[primary] % ids[primary],
        }
        # 別名（主表記以外を3つまで）
        alt = [STEL_FORMAT[k] % ids[k] for k in STEL_PRIORITY if k != primary and k in ids][:3]
        rec["alt"] = ", ".join(alt)
        # 通称（いずれかの番号で names.dat に載っていれば）
        for k in STEL_PRIORITY:
            if k not in ids:
                continue
            n = names.get(k + ":" + norm_id(ids[k]))
            if n:
                rec["en"] = n[0]
                for e in n:
                    if e in ja:
                        rec["jaStel"] = ja[e]
                        break
                break
        out.append(rec)
    if pos[0] != n_total or seq < 1000:
        raise StellariumFormatError("Unsupported Stellarium catalog format (unexpected end)")
    return {"list": out, "ja": ja, "version": version}


def _parse_hms(s):
    h, m, sec = s.split(":")
    return (int(h) + int(m) / 60.0 + float(sec) / 3600.0) * 15.0


def _parse_dms(s):
    sign = -1.0 if s.startswith("-") else 1.0
    d, m, sec = s.lstrip("+-").split(":")
    return sign * (int(d) + int(m) / 60.0 + float(sec) / 3600.0)


def load_boundaries(directory, step_deg=0.5):
    """skycultures/modern/index.json の星座境界（IAU, B1875）を読み、J2000 の折れ線にする。

    境界は B1875 の赤経一定線（M）と赤緯一定線（P）でできているので、B1875 のまま
    補間してから J2000 に歳差補正する（赤緯一定線は大円ではないため）。
    返り値: [{"c1": "AND", "c2": "LAC", "pts": [(ra, dec), ...]}]（取得できなければ []）
    """
    path = os.path.join(directory, "skycultures", "modern", "index.json")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    edges = data.get("edges") or []
    epoch = str(data.get("edges_epoch") or "B1875")
    if not edges:
        return []
    import numpy as np
    segs = []
    all_ra = []
    all_dec = []
    for e in edges:
        p = e.split()
        if len(p) < 8:
            continue
        kind = p[1][0].upper()
        ra1, de1, ra2, de2 = _parse_hms(p[2]), _parse_dms(p[3]), _parse_hms(p[4]), _parse_dms(p[5])
        c1, c2 = p[6].upper(), p[7].upper()
        if kind == "P":
            # 赤緯一定線: 赤経を補間（0h をまたぐ場合は短い方へ）
            dra = (ra2 - ra1 + 540.0) % 360.0 - 180.0
            n = max(1, int(abs(dra) * np.cos(np.radians(de1)) / step_deg) + 1)
            ras = [ra1 + dra * i / n for i in range(n + 1)]
            decs = [de1] * (n + 1)
        else:
            n = max(1, int(abs(de2 - de1) / step_deg) + 1)
            ras = [ra1] * (n + 1)
            decs = [de1 + (de2 - de1) * i / n for i in range(n + 1)]
        segs.append((c1, c2, len(ras)))
        all_ra.extend(ras)
        all_dec.extend(decs)
    # B1875 (FK4) → ICRS
    from astropy.coordinates import SkyCoord, FK4
    import astropy.units as u
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sc = SkyCoord(np.array(all_ra) * u.deg, np.array(all_dec) * u.deg,
                      frame=FK4(equinox=epoch, obstime=epoch)).icrs
    ra_j, dec_j = sc.ra.deg, sc.dec.deg
    out = []
    k = 0
    for c1, c2, n in segs:
        out.append({"c1": c1, "c2": c2, "pts": list(zip(ra_j[k:k + n].tolist(), dec_j[k:k + n].tolist()))})
        k += n
    return out
