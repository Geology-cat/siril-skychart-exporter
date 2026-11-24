# -*- coding: utf-8 -*-
"""統合カタログ。

星雲・星団・銀河は Stellarium のカタログ（約9.5万天体・種別つき）を優先し、
見つからなければ Siril 同梱のカタログ（Messier / NGC / IC / Sh2 / LDN）を使う。
恒星・星座線・星座名は Siril 同梱のカタログ、星座境界は Stellarium の
skycultures から読む（Siril には境界データが無い）。

PixInsight 版 SCE-Catalog.jsh の移植。PixInsight の同梱カタログ・Gaia プロセスの
代わりに Siril / Stellarium / astropy を使う。
"""

import csv
import json
import math
import os
import re
import time

from . import names as N
from . import stellarium as stel
from .astro import ang_dist, norm_ra, D2R, R2D

# NGC/IC 番号を持たない一部のメシエ天体を含む、メシエ天体の種別
# （Siril 同梱カタログには種別が無いため、Stellarium を使わないときに使う）
MESSIER_TYPES = {}
for _t, _ms in {
    "globularCluster": [2, 3, 4, 5, 9, 10, 12, 13, 14, 15, 19, 22, 28, 30, 53, 54, 55, 56, 62, 68, 69, 70, 71, 72,
                        75, 79, 80, 92, 107],
    "openCluster": [6, 7, 11, 18, 21, 23, 25, 26, 29, 34, 35, 36, 37, 38, 39, 41, 44, 45, 46, 47, 48, 50, 52, 67,
                    93, 103],
    "galaxy": [31, 32, 33, 49, 51, 58, 59, 60, 61, 63, 64, 65, 66, 74, 77, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90,
               91, 94, 95, 96, 98, 99, 100, 101, 102, 104, 105, 106, 108, 109, 110],
    "planetaryNebula": [27, 57, 76, 97],
    "emissionNebula": [8, 17, 20, 42, 43],
    "clusterNebula": [16],
    "reflectionNebula": [78],
    "snr": [1],
    "starCloud": [24],
    "doubleStar": [40],
    "asterism": [73],
}.items():
    for _m in _ms:
        MESSIER_TYPES["M%d" % _m] = _t

_ID_TOKEN = re.compile(r"^(M|NGC|IC|Sh2-|LdN|LDN|C|Mel|Cr|B)\s*\d", re.I)


def norm_ngc(s):
    """"NGC 7000" / "ngc7000" / "IC 434" → "NGC7000" / "IC434"。該当しなければ None。"""
    if not s:
        return None
    m = re.search(r"\b(NGC|IC)\s*0*(\d+)\s*([A-Za-z]?)\b", s, re.I)
    if not m:
        return None
    return m.group(1).upper() + m.group(2) + (m.group(3).upper() if m.group(3) else "")


def spaced_id(ident):
    """"NGC7000" → "NGC 7000" """
    m = re.match(r"^(NGC|IC)(\d+.*)$", ident or "")
    return m.group(1) + " " + m.group(2) if m else ident


def _num(s):
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def read_csv(path):
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]


def parse_star_id(ident):
    """"alf CMa" → ("α CMa", "CMA")、"bet01 Cyg" → "β¹ Cyg"、"61 Cyg" → "61 Cyg"、"CG And" → "CG And" """
    s = ident.strip()
    # 連星の成分記号（"alf Cen A" の A）は表示の末尾に残す
    comp = ""
    mc = re.match(r"^(.*\s[A-Z][a-zA-Z]{2})\s+([A-D])$", s)
    if mc:
        s, comp = mc.group(1), " " + mc.group(2)
    m = re.match(r"^(\d+\s+)?([A-Za-z]+)\.?(\d*)\s+([A-Z][a-zA-Z]{2})$", s)
    if m:
        key = m.group(2).lower()
        con = m.group(4).upper()
        if key in N.GREEK:
            sup = ""
            if m.group(3):
                sup = "".join(N.SUPERSCRIPT_DIGITS[int(ch)] for ch in str(int(m.group(3))))
            return N.GREEK[key] + sup + " " + m.group(4) + comp, con
        if len(m.group(2)) == 1 and not m.group(1):
            return m.group(2) + " " + m.group(4) + comp, con
    f = re.match(r"^(\d+)\s+([A-Z][a-zA-Z]{2})$", s)
    if f:
        return f.group(1) + " " + f.group(2) + comp, f.group(2).upper()
    c = re.search(r"\s([A-Z][a-zA-Z]{2})$", s)
    return s + comp, (c.group(1).upper() if c else "")


def pick_proper_name(alias):
    """"Dog Star/Sirius/Sirius A" のような複数の通称から、表示する1つを選ぶ"""
    names = [t.strip() for t in (alias or "").split("/") if t.strip()]
    for t in names:
        if t in N.STAR_NAMES_JA:
            return t
    return names[0] if names else ""


def find_siril_catalogue_dir(system_data_dir=""):
    """Siril 同梱カタログ（share/siril/catalogue）のフォルダを探す"""
    cands = []
    if system_data_dir:
        cands += [os.path.join(system_data_dir, "catalogue"), system_data_dir]
    cands += [
        "/Applications/Siril.app/Contents/Resources/share/siril/catalogue",
        "C:/Program Files/Siril/share/siril/catalogue",
        "/usr/share/siril/catalogue",
        "/usr/local/share/siril/catalogue",
        "/app/share/siril/catalogue",
    ]
    for c in cands:
        if os.path.isfile(os.path.join(c, "stars.csv")):
            return c
    return ""


def _rank(rec):
    return 0 if rec.get("m") else (1 if rec.get("c") else (2 if (rec.get("ja") or rec.get("en")) else
                                                             (3 if rec.get("ngc") else 4)))


def _sort_mag(rec):
    if rec.get("mag") is not None:
        return rec["mag"]
    if rec.get("diam") is not None:
        return 13 - 2.5 * math.log10(max(rec["diam"], 0.1))
    return 16


class Catalog:
    def __init__(self, log=print):
        self.log = log
        self.dso = []            # 星雲・星団・銀河
        self.stars = []          # 恒星（明るい順）
        self.const_lines = []    # [[(ra,dec),(ra,dec)], ...]
        self.const_borders = []  # [{"c1","c2","pts"}]
        self.const_centers = {}  # "ORI" → (ra, dec)
        self.dso_key = None
        self.basic_loaded = False
        self.source = "siril"    # "stellarium" | "siril"
        self.stellarium_dir = ""
        self.siril_dir = ""
        self.stel_ja = {}
        self.cache_dir = ""

    # ------------------------------------------------------------------------

    def load(self, use_stellarium=True, stellarium_dir="", siril_data_dir="", cache_dir=""):
        t0 = time.time()
        self.cache_dir = cache_dir or ""
        self.siril_dir = find_siril_catalogue_dir(siril_data_dir)
        if not self.siril_dir:
            raise RuntimeError("Siril catalogue folder (share/siril/catalogue) not found")
        sdir = stel.find_stellarium_dir(stellarium_dir) if use_stellarium else ""
        key = sdir or "siril"
        if self.dso_key != key:
            self.source = "siril"
            self.stellarium_dir = ""
            self.stel_ja = {}
            self.dso = []
            self.const_borders = []
            if sdir:
                try:
                    self._load_stellarium_dso(sdir)
                    self.source = "stellarium"
                    self.stellarium_dir = sdir
                except Exception as x:  # noqa: BLE001
                    self.log("SkyChartExporter: Stellarium catalog could not be loaded: %s" % x)
            if self.source != "stellarium":
                self._load_siril_dso()
            if sdir:
                try:
                    self.const_borders = self._load_borders(sdir)
                except Exception as x:  # noqa: BLE001
                    self.log("SkyChartExporter: constellation borders could not be loaded: %s" % x)
            self.dso_key = key
        if not self.basic_loaded:
            self._load_stars()
            self._load_constellations()
            self.basic_loaded = True
        # 恒星の日本語名: 辞書 → Stellarium の訳
        for s in self.stars:
            s["ja"] = (N.STAR_NAMES_JA.get(s["proper"]) or self.stel_ja.get(s["proper"]) or "") if s["proper"] else ""
        self.log("SkyChartExporter: catalogs ready (%s): %d DSO, %d stars, %d constellation lines, %d border "
                 "segments (%.2f s)" % (self.source, len(self.dso), len(self.stars), len(self.const_lines),
                                        len(self.const_borders), time.time() - t0))

    # ------------------------------------------------------------------------

    def _load_stellarium_dso(self, directory):
        r = stel.load_dso(directory, self.log)
        self.stel_ja = r["ja"]
        for rec in r["list"]:
            ids = rec["ids"]
            keys = [rec["m"], rec["ngc"], ("IC" + ids["IC"]) if "IC" in ids else None, rec["c"],
                    ("Sh2-" + ids["Sh2"]) if "Sh2" in ids else None, ("Cr" + ids["Cr"]) if "Cr" in ids else None,
                    ("Mel" + ids["Mel"]) if "Mel" in ids else None]
            for k in keys:
                if not k:
                    continue
                n = N.DSO_NAMES.get(k)
                if n:
                    if n.get("ja") and not rec["ja"]:
                        rec["ja"] = n["ja"]
                    if n.get("en") and not rec["en"]:
                        rec["en"] = n["en"]
                if rec["diam"] is None and k in N.SIZE_FIXUPS:
                    rec["diam"] = N.SIZE_FIXUPS[k]
            if not rec["ja"] and rec["jaStel"]:
                rec["ja"] = rec["jaStel"]
            if rec["m"] and rec["m"] in MESSIER_TYPES and rec["type"] in ("other", "star"):
                rec["type"] = MESSIER_TYPES[rec["m"]]
            # 中心星の種別が付いている星雲（例: Sh2-308）は星雲として扱う
            if rec["type"] == "star":
                if "VdB" in ids:
                    rec["type"] = "reflectionNebula"
                elif any(k in ids for k in ("Sh2", "LBN", "RCW", "Ced", "DWB")):
                    rec["type"] = "nebula"
            rec["rank"] = _rank(rec)
            rec["sortMag"] = _sort_mag(rec)
        self.log("SkyChartExporter: Stellarium catalog %s: %d objects" % (r["version"], len(r["list"])))
        self.dso = r["list"]

    def _load_siril_dso(self):
        """Siril 同梱の Messier / NGC / IC / Sh2 / LDN を統合する（種別は Messier・Sh2・LDN のみ）"""
        d = self.siril_dir
        by_key = {}
        out = []

        def add(key, rec):
            rec["key"] = key
            by_key[key] = rec
            out.append(rec)
            return rec

        def base(ra, dec, **kw):
            rec = {"m": None, "c": None, "ngc": None, "sup": None, "ra": ra, "dec": dec, "mag": None,
                   "diam": None, "axisRatio": None, "pa": None, "type": "unknown", "en": "", "ja": "", "ids": {},
                   "isSNR": False}
            rec.update(kw)
            return rec

        def split_alias(alias):
            idents, common = [], []
            for tok in (alias or "").split("/"):
                tok = tok.strip()
                if not tok:
                    continue
                (idents if _ID_TOKEN.match(tok) else common).append(tok)
            return idents, common

        # 1) NGC / IC（別名で重複している番号は1件にまとめる）
        for fname in ("ngc.csv", "ic.csv"):
            path = os.path.join(d, fname)
            if not os.path.isfile(path):
                continue
            for r in read_csv(path):
                name = r.get("name") or ""
                # "NGC4236_1" / "NGC4236_2" のような成分番号: _1 は本体、それ以外は "NGC4236-2"
                mc = re.match(r"^(NGC|IC)(\d+[A-Z]?)_(\d+)$", name, re.I)
                if mc:
                    b = mc.group(1).upper() + mc.group(2).upper()
                    ident = b if (mc.group(3) == "1" and b not in by_key) else b + "-" + mc.group(3)
                else:
                    ident = norm_ngc(name) or name
                ra, dec = _num(r.get("ra")), _num(r.get("dec"))
                if ra is None or dec is None or ident in by_key:
                    continue
                idents, common = split_alias(r.get("alias"))
                dup = None
                for a in idents:
                    na = norm_ngc(a)
                    if na and na in by_key and ang_dist(ra, dec, by_key[na]["ra"], by_key[na]["dec"]) < 0.05:
                        dup = by_key[na]
                        break
                if dup is not None:
                    by_key[ident] = dup
                    continue
                rec = add(ident, base(ra, dec, ngc=ident, mag=_num(r.get("mag")), diam=_num(r.get("diameter")),
                                      en=common[0] if common else ""))
                for a in idents:
                    if a.lower().startswith("sh2-"):
                        rec["ids"]["Sh2"] = a[4:]

        # 2) Messier（NGC/IC 番号があればそのレコードに統合する）
        path = os.path.join(d, "messier.csv")
        for r in read_csv(path) if os.path.isfile(path) else []:
            mid = r.get("name", "").replace(" ", "")
            ra, dec = _num(r.get("ra")), _num(r.get("dec"))
            if ra is None or dec is None:
                continue
            idents, common = split_alias(r.get("alias"))
            ngc = None
            for a in idents:
                ngc = norm_ngc(a)
                if ngc:
                    break
            rec = by_key.get(ngc) if ngc else None
            if rec is None:
                rec = add(mid, base(ra, dec, ngc=ngc))
            rec["m"] = mid
            # メシエカタログの値を優先する（位置・明るさ・大きさ）
            rec["ra"], rec["dec"] = ra, dec
            if _num(r.get("mag")) is not None:
                rec["mag"] = _num(r.get("mag"))
            if _num(r.get("diameter")):
                rec["diam"] = _num(r.get("diameter"))
            if common:
                rec["en"] = common[0]
            if mid in MESSIER_TYPES:
                rec["type"] = MESSIER_TYPES[mid]
            by_key[mid] = rec

        # 3) Sh2（散光星雲。M/NGC/IC と同じものは統合）
        path = os.path.join(d, "sh2.csv")
        for r in read_csv(path) if os.path.isfile(path) else []:
            sid = r.get("name", "").replace(" ", "")
            ra, dec = _num(r.get("ra")), _num(r.get("dec"))
            if ra is None or dec is None:
                continue
            idents, common = split_alias(r.get("alias"))
            rec = None
            for a in idents:
                k = a.replace(" ", "")
                k = norm_ngc(k) or k
                if k in by_key and ang_dist(ra, dec, by_key[k]["ra"], by_key[k]["dec"]) < 1.0:
                    rec = by_key[k]
                    break
            if rec is None:
                rec = add(sid, base(ra, dec, sup=sid.replace("Sh2-", "Sh2-"), type="nebula"))
            rec["ids"]["Sh2"] = sid[4:]
            if common and not rec["en"]:
                rec["en"] = common[0]
            by_key[sid] = rec

        # 4) LDN（暗黒星雲。大きさの情報は無い）
        path = os.path.join(d, "ldn.csv")
        for r in read_csv(path) if os.path.isfile(path) else []:
            ra, dec = _num(r.get("ra")), _num(r.get("dec"))
            m = re.match(r"^LdN-?(\d+)$", r.get("name", ""), re.I)
            if ra is None or dec is None or not m:
                continue
            lid = "LDN " + m.group(1)
            add(lid, base(ra, dec, sup=lid, type="darkNebula", ids={"LDN": m.group(1)}))

        # 5) 補助カタログ
        for s in N.SUPPLEMENT:
            if s["id"] in by_key:
                continue
            add(s["id"], base(s["ra"], s["dec"], sup=s["label"], diam=s.get("diam"),
                              axisRatio=s.get("axisRatio"), type=s["type"], isSNR=s["type"] == "snr"))

        # 6) 名称辞書と補正値の適用
        for rec in out:
            keys = [rec["m"], rec["ngc"], rec["c"], rec["key"]]
            if "Sh2" in rec["ids"]:
                keys.append("Sh2-" + rec["ids"]["Sh2"])
            for k in keys:
                n = N.DSO_NAMES.get(k) if k else None
                if n:
                    if n.get("ja") and not rec["ja"]:
                        rec["ja"] = n["ja"]
                    if n.get("en") and not rec["en"]:
                        rec["en"] = n["en"]
            for k in keys:
                if k and k in N.SIZE_FIXUPS:
                    rec["diam"] = N.SIZE_FIXUPS[k]
                    break
            if rec["type"] == "snr":
                rec["isSNR"] = True
            rec["rank"] = _rank(rec)
            rec["sortMag"] = _sort_mag(rec)
        self.dso = out

    def _load_borders(self, sdir):
        """星座境界（Stellarium の skycultures）。歳差補正に数秒かかるのでキャッシュする。"""
        src = os.path.join(sdir, "skycultures", "modern", "index.json")
        if not os.path.isfile(src):
            return []
        stamp = "%s|%d|%d" % (src, os.path.getsize(src), int(os.path.getmtime(src)))
        cache = os.path.join(self.cache_dir, "SkyChartExporter-borders.json") if self.cache_dir else ""
        if cache and os.path.isfile(cache):
            try:
                with open(cache, encoding="utf-8") as f:
                    c = json.load(f)
                if c.get("stamp") == stamp and c.get("version") == 1:
                    return [{"c1": b[0], "c2": b[1], "pts": [tuple(p) for p in b[2]]} for b in c["borders"]]
            except Exception:  # noqa: BLE001
                pass
        borders = stel.load_boundaries(sdir)
        if cache and borders:
            try:
                os.makedirs(self.cache_dir, exist_ok=True)
                with open(cache, "w", encoding="utf-8") as f:
                    json.dump({"version": 1, "stamp": stamp,
                               "borders": [[b["c1"], b["c2"], [[round(p[0], 5), round(p[1], 5)] for p in b["pts"]]]
                                           for b in borders]}, f)
            except OSError:
                pass
        return borders

    def _load_stars(self):
        self.stars = []
        for r in read_csv(os.path.join(self.siril_dir, "stars.csv")):
            ra, dec, mag = _num(r.get("ra")), _num(r.get("dec")), _num(r.get("mag"))
            if ra is None or dec is None or mag is None:
                continue
            label, con = parse_star_id(r.get("name", ""))
            self.stars.append({"ra": ra, "dec": dec, "mag": mag, "id": r.get("name", ""), "bayer": label,
                               "con": con, "proper": pick_proper_name(r.get("alias")), "ja": "",
                               "type": "star"})
        # 明るい順
        self.stars.sort(key=lambda s: s["mag"])

    def _load_constellations(self):
        self.const_lines = []
        path = os.path.join(self.siril_dir, "constellations.csv")
        if os.path.isfile(path):
            for r in read_csv(path):
                v = [_num(r.get(k)) for k in ("ra", "dec", "ra1", "dec1")]
                if None not in v:
                    self.const_lines.append([(v[0], v[1]), (v[2], v[3])])
        self.const_centers = {}
        path = os.path.join(self.siril_dir, "constellationsnames.csv")
        if os.path.isfile(path):
            for r in read_csv(path):
                ra, dec = _num(r.get("ra")), _num(r.get("dec"))
                abbr = (r.get("alias") or "").upper()
                if ra is not None and dec is not None and abbr in N.CONSTELLATIONS:
                    self.const_centers[abbr] = (ra, dec)

    # ------------------------------------------------------------------------

    def dso_arrays(self):
        """星雲・星団・銀河の赤経・赤緯の numpy 配列（星図の絞り込み用にキャッシュ）"""
        import numpy as np
        if getattr(self, "_arr_for", None) is not self.dso:
            self._arr = (np.array([o["ra"] for o in self.dso], dtype=float),
                         np.array([o["dec"] for o in self.dso], dtype=float))
            self._arr_for = self.dso
        return self._arr

    @staticmethod
    def find_constellation(ra, dec):
        """指定座標が属する星座の略号（大文字）。astropy の IAU 境界判定を使う。判定できなければ ""。"""
        try:
            from astropy.coordinates import SkyCoord, get_constellation
            import astropy.units as u
            c = get_constellation(SkyCoord(ra * u.deg, dec * u.deg, frame="icrs"), short_name=True)
            return str(c).upper()
        except Exception:  # noqa: BLE001
            return ""


# ----------------------------------------------------------------------------
# 表示用ヘルパー
# ----------------------------------------------------------------------------

def dso_designation(o):
    if o.get("desig"):
        return o["desig"]
    if o.get("m"):
        return o["m"]
    if o.get("ngc"):
        return spaced_id(o["ngc"])
    if o.get("c"):
        return o["c"]
    return o.get("sup") or o.get("key")


def dso_alt_ids(o):
    """一覧表示用の別名（"NGC 1976" など、主表記以外のカタログ番号）"""
    if o.get("alt") is not None:
        return o["alt"]
    ids = []
    primary = dso_designation(o)
    if o.get("m") and o["m"] != primary:
        ids.append(o["m"])
    if o.get("ngc") and spaced_id(o["ngc"]) != primary:
        ids.append(spaced_id(o["ngc"]))
    if o.get("ids", {}).get("Sh2") and "Sh2-" + o["ids"]["Sh2"] != primary:
        ids.append("Sh2-" + o["ids"]["Sh2"])
    if o.get("c") and o["c"] != primary:
        ids.append(o["c"])
    return ", ".join(ids)


def dso_name(o, lang):
    if lang == "ja":
        return o.get("ja") or o.get("en") or ""
    return o.get("en") or ""


def star_name(s, lang):
    if lang == "ja":
        return s.get("ja") or s.get("proper") or s.get("bayer")
    return s.get("proper") or s.get("bayer")


def type_name(type_key, lang):
    t = N.TYPES.get(type_key) or N.TYPES["other"]
    return t[0] if lang == "ja" else t[1]


def constellation_name(abbr, lang):
    c = N.CONSTELLATIONS.get((abbr or "").upper())
    if not c:
        return abbr or ""
    return c[0] if lang == "ja" else c[1]
