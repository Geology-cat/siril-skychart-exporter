# -*- coding: utf-8 -*-
"""ページに載せる内容（解像度に依存しないデータ）。PixInsight 版 SCE-Render.jsh の SceModel の移植。"""

import math

from . import names as N
from .astro import format_ra, format_dec, format_angle, format_datetime, format_duration
from .catalog import (dso_designation, dso_alt_ids, dso_name, type_name, constellation_name, Catalog)
from .options import PRESETS
from .strings import tr as _tr, fmt


def type_group(t):
    if t in ("galaxy", "galaxyPair", "galaxyTriplet", "galaxyGroup", "interactingGalaxy", "quasar", "galaxyCluster"):
        return "galaxy"
    if t in ("nebula", "emissionNebula", "hii", "reflectionNebula", "clusterNebula"):
        return "nebula"
    if t == "planetaryNebula":
        return "pn"
    if t in ("openCluster", "association", "asterism", "starCloud", "doubleStar", "starCluster"):
        return "oc"
    if t == "globularCluster":
        return "gc"
    if t == "snr":
        return "snr"
    if t in ("darkNebula", "molecularCloud"):
        return "dark"
    return "other"


def mag_passes(o, limit):
    if limit >= 20:
        return True
    if o.get("mag") is not None:
        return o["mag"] <= limit
    # 等級のない天体（主に散光星雲）は大きさで判断
    return (o.get("diam") or 0) >= max(1, 16 - limit)


def _opt(opts, k):
    return opts[k] if isinstance(opts, dict) else getattr(opts, k)


def dso_passes(o, opts):
    g = lambda k: _opt(opts, k)  # noqa: E731
    if o.get("m") and g("aMessier"):
        return True
    if o.get("c") and g("aCaldwell"):
        return True
    if (o.get("ja") or o.get("en")) and g("aNamed"):
        return True
    if o.get("ngc") and g("aNGC") and mag_passes(o, g("aNGCMag")):
        return True
    if o.get("isSNR") and g("aSNR"):
        return True
    ids = o.get("ids") or {}
    if not ids:
        return False
    has = lambda *ks: any(k in ids for k in ks)  # noqa: E731
    if g("aBrightNeb") and has("Sh2", "LBN", "VdB", "RCW", "Ced", "DWB", "VdBH"):
        return True
    if g("aDarkNeb") and has("B", "LDN"):
        return True
    if g("aPN") and has("PK", "PNG"):
        return True
    if g("aClusters") and has("Cr", "Mel", "Tr", "St", "Ru", "VdBHa"):
        return True
    if g("aGalaxies") and has("PGC", "UGC", "ESO", "Arp", "VV", "HCG") and mag_passes(o, g("aGalaxyMag")):
        return True
    if g("aGalaxyClusters") and has("ACO"):
        return True
    return False


def any_object_layer(opts):
    return any(getattr(opts, k) for k in ("aMessier", "aCaldwell", "aNamed", "aNGC", "aSNR", "aBrightNeb",
                                          "aDarkNeb", "aPN", "aClusters", "aGalaxies", "aGalaxyClusters"))


def annotations_enabled(opts):
    return any_object_layer(opts) or any(getattr(opts, k) for k in (
        "aStars", "aConstLines", "aConstBorders", "aConstNames", "aGrid", "aCompass"))


class Model:
    def __init__(self, info, catalog, opts, software=""):
        self.opts = opts
        self.lang = opts.lang
        self.catalog = catalog
        self.info = info
        self.software = software
        info.resolve_focal(opts)
        self.constellation = Catalog.find_constellation(info.ra, info.dec)
        self.build_objects()
        self.chart_fov = self.compute_chart_fov()
        self.compute_chart_stars()
        self.title = (opts.title or "").strip() or self.auto_title()
        self.info_rows = self.build_info_rows()

    def tr(self, k):
        return _tr(k, self.lang)

    def build_objects(self):
        info, opts = self.info, self.opts
        size_deg = lambda o: (o.get("diam") or 0) / 60  # noqa: E731
        self.all_dso = info.find_in_image(self.catalog.dso, size_deg)

        def prio(e):
            return (e[0]["rank"], e[0]["sortMag"])

        self.annot_dso = sorted([e for e in self.all_dso if dso_passes(e[0], opts)], key=prio)

        star_limit = max(opts.aStarMag if opts.aStars else 0, 4.5 if opts.listStars else 0)
        stars = info.find_in_image([s for s in self.catalog.stars if s["mag"] <= star_limit])
        stars.sort(key=lambda e: e[0]["mag"])
        self.annot_stars = [e for e in stars if e[0]["mag"] <= opts.aStarMag] if opts.aStars else []

        # 一覧: アノテーション設定で天体を選んでいなければ標準の条件を使う
        flt = (lambda o: dso_passes(o, opts)) if any_object_layer(opts) else \
            (lambda o: dso_passes(o, PRESETS["standard"]))
        lst = sorted([e for e in self.all_dso if flt(e[0])], key=prio)
        self.list_items = [{
            "desig": dso_designation(o), "alt": dso_alt_ids(o), "name": dso_name(o, self.lang),
            "type": type_name(o["type"], self.lang), "group": type_group(o["type"]), "mag": o.get("mag"),
        } for o, _, _ in lst]
        if opts.listStars:
            for o, _, _ in stars:
                if o["proper"] and o["mag"] <= 4.5:
                    self.list_items.append({
                        "desig": o["bayer"], "alt": "",
                        "name": (o["ja"] or o["proper"]) if self.lang == "ja" else o["proper"],
                        "type": type_name("star", self.lang) + " %.1f" % o["mag"],
                        "group": "star", "mag": o["mag"],
                    })

    def compute_chart_fov(self):
        info, opts = self.info, self.opts
        span = max(info.fov_w, info.fov_h)
        if opts.chartFovMode == "factor":
            fov = span * opts.chartFovFactor
        elif opts.chartFovMode == "fixed":
            fov = opts.chartFovDeg
        else:
            fov = span * 1.8 if span > 20 else max(15, min(50, span * 6))
        return max(max(span * 1.1, 0.5), min(150, fov))

    def compute_chart_stars(self):
        """星図の恒星は Siril 同梱の恒星表（約3,600星）。Gaia は Siril の Python API から範囲検索できないため使わない。"""
        fov, opts = self.chart_fov, self.opts
        self.chart_star_limit = opts.chartStarMag if opts.chartStarMag > 0 else \
            (4.8 if fov >= 60 else (5.5 if fov >= 30 else (6.0 if fov >= 12 else 6.5)))

    def auto_title(self):
        info = self.info
        best, best_score = None, float("inf")
        diag = math.hypot(info.width, info.height) / 2
        for o, x, y in self.all_dso:
            if o["rank"] > 2:
                continue
            dist = math.hypot(x - info.width / 2, y - info.height / 2) / diag
            size = min(1, (o.get("diam") or 0) / 60 / info.scale_deg / min(info.width, info.height))
            score = o["rank"] * 0.6 + dist * 1.5 - size * 1.2
            if score < best_score:
                best_score, best = score, o
        if best:
            name = dso_name(best, self.lang)
            return dso_designation(best) + ("  " + name if name else "")
        return info.object or info.id

    def build_info_rows(self):
        L, info, opts = self.lang, self.info, self.opts
        tr = self.tr
        rows = []
        sys_ = info.reference_system.upper()
        sys_s = " (J2000)" if sys_ in ("ICRS", "FK5") else " (%s)" % info.reference_system
        rows.append((tr("rRA"), format_ra(info.ra) + sys_s))
        rows.append((tr("rDec"), format_dec(info.dec)))
        if self.constellation:
            c = N.CONSTELLATIONS.get(self.constellation)
            rows.append((tr("rConst"), constellation_name(self.constellation, L) +
                         ("（%s）" % c[1] if (L == "ja" and c) else "")))
        rows.append((tr("rFov"), format_angle(info.fov_w) + " × " + format_angle(info.fov_h)))
        rows.append((tr("rScale"), "%.2f″/px" % info.scale_arcsec))

        f = info.focal
        if opts.focalOverride > 0:
            focal = "%.0f mm" % opts.focalOverride + tr("focalManual")
        elif info.focal_nominal:
            focal = "%.0f mm" % info.focal_nominal
            fe = info.focal_effective
            if fe and abs(fe - info.focal_nominal) / info.focal_nominal > 0.005:
                focal += fmt("（%s %.0f mm）" if L == "ja" else " (%s %.0f mm)", tr("focalEffective"), fe)
        elif f and f["source"] == "fov35":
            focal = tr("approx") + "%.0f mm" % f["value"] + tr("focalFromFov")
        elif f and f["value"]:
            pxs = ("%.2f" % f["px"]).rstrip("0").rstrip(".")
            focal = "%.0f mm" % f["value"]
            src = f["pxSource"]
            if src == "camera":
                focal += fmt(tr("focalFromCamera"), info.camera, pxs)
            elif src == "remembered":
                focal += fmt(tr("focalFromRemembered"), pxs)
            elif src == "manual":
                focal += fmt(tr("focalFromManualPx"), pxs)
            else:
                focal += fmt(tr("focalFromHeaderPx"), pxs)
        else:
            focal = tr("focalUnknown")
        rows.append((tr("rFocal"), focal))

        if info.rotation is not None:
            rows.append((tr("rRotation"), "%.1f°" % info.rotation + (tr("flipped") if info.flipped else "")))
        rows.append((tr("rSize"), "%d × %d px" % (info.width, info.height)))
        if info.date_obs:
            rows.append((tr("rDate"), format_datetime(info.date_obs)))
        e = ""
        if info.exposure and info.exposure > 0:
            e = format_duration(info.exposure)
            if info.n_subs and info.n_subs > 1:
                total = info.livetime if (info.livetime and info.livetime > 0) else info.exposure * info.n_subs
                e += " × %d = %s" % (info.n_subs, format_duration(total))
        elif info.livetime and info.livetime > 0:
            e = format_duration(info.livetime)
        if e:
            rows.append((tr("rExposure"), e))
        if info.telescope:
            rows.append((tr("rTelescope"), info.telescope))
        if info.camera:
            rows.append((tr("rCamera"), info.camera))
        if info.filter:
            rows.append((tr("rFilter"), info.filter))
        site = (opts.site or "").strip()
        if not site and info.site_lat is not None:
            site = "%.4f°%s, %.4f°%s" % (abs(info.site_lat), "N" if info.site_lat >= 0 else "S",
                                        abs(info.site_lon), "E" if info.site_lon >= 0 else "W")
        if site:
            rows.append((tr("rSite"), site))
        observer = (opts.observer or "").strip() or info.observer
        if observer:
            rows.append((tr("rObserver"), observer))
        return rows
