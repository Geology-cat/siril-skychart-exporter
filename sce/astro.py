# -*- coding: utf-8 -*-
"""球面天文の計算（角距離・投影）と、座標・日時の書式化。

PixInsight 版 SCE-Astro.jsh の移植。Siril にも PixInsight にも依存しない。
"""

import math
from datetime import datetime, timezone

D2R = math.pi / 180.0
R2D = 180.0 / math.pi


def norm_ra(a):
    """赤経を [0,360) に正規化する"""
    a = math.fmod(a, 360.0)
    return a + 360.0 if a < 0 else a


def ang_dist(ra1, dec1, ra2, dec2):
    """2点間の角距離（度）。ハバーサイン公式で小角でも精度を保つ。"""
    d1, d2 = dec1 * D2R, dec2 * D2R
    sdd = math.sin((d2 - d1) / 2)
    sda = math.sin((ra2 - ra1) * D2R / 2)
    h = sdd * sdd + math.cos(d1) * math.cos(d2) * sda * sda
    return 2 * math.asin(min(1.0, math.sqrt(max(0.0, h)))) * R2D


def ang_dist_np(ra0, dec0, ra, dec):
    """numpy 版の角距離（度）。ra, dec は配列。"""
    import numpy as np
    d0 = dec0 * D2R
    d = np.asarray(dec, dtype=float) * D2R
    sdd = np.sin((d - d0) / 2)
    sda = np.sin((np.asarray(ra, dtype=float) - ra0) * D2R / 2)
    h = sdd * sdd + math.cos(d0) * np.cos(d) * sda * sda
    return 2 * np.arcsin(np.minimum(1.0, np.sqrt(np.maximum(0.0, h)))) * R2D


def position_angle(ra0, dec0, ra, dec):
    """(ra0,dec0) から見た (ra,dec) の位置角（度、北から東回り）"""
    d0, d, da = dec0 * D2R, dec * D2R, (ra - ra0) * D2R
    y = math.sin(da) * math.cos(d)
    x = math.cos(d0) * math.sin(d) - math.sin(d0) * math.cos(d) * math.cos(da)
    pa = math.atan2(y, x) * R2D
    return pa + 360.0 if pa < 0 else pa


def offset_point(ra0, dec0, pa, dist):
    """(ra0,dec0) から位置角 pa の方向へ角距離 dist（度）だけ進んだ点"""
    d0, r, p = dec0 * D2R, dist * D2R, pa * D2R
    sd = math.sin(d0) * math.cos(r) + math.cos(d0) * math.sin(r) * math.cos(p)
    d = math.asin(max(-1.0, min(1.0, sd)))
    da = math.atan2(math.sin(p) * math.sin(r) * math.cos(d0), math.cos(r) - math.sin(d0) * sd)
    return norm_ra(ra0 + da * R2D), d * R2D


def great_circle_samples(ra1, dec1, ra2, dec2, max_step):
    """大円に沿って2点間を補間した点列（両端を含む）"""
    dist = ang_dist(ra1, dec1, ra2, dec2)
    n = max(1, int(math.ceil(dist / max_step)))
    if n == 1:
        return [(ra1, dec1), (ra2, dec2)]
    pa = position_angle(ra1, dec1, ra2, dec2)
    pts = [(ra1, dec1)]
    for i in range(1, n):
        pts.append(offset_point(ra1, dec1, pa, dist * i / n))
    pts.append((ra2, dec2))
    return pts


def densify(pts, step):
    """折れ線の各区間を大円で補間する"""
    out = []
    for i in range(len(pts) - 1):
        seg = great_circle_samples(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], step)
        if i > 0:
            seg = seg[1:]
        out.extend(seg)
    return out


class StereoProjection:
    """立体射影（ステレオ投影）。星図の描画に使う。
    project() は東を +x、北を +y とする単位（2tan(c/2)）で返す。"""

    def __init__(self, ra0, dec0):
        self.ra0 = ra0
        self.dec0 = dec0
        self.sd0 = math.sin(dec0 * D2R)
        self.cd0 = math.cos(dec0 * D2R)

    def project(self, ra, dec):
        d, da = dec * D2R, (ra - self.ra0) * D2R
        sd, cd, cda = math.sin(d), math.cos(d), math.cos(da)
        cosc = self.sd0 * sd + self.cd0 * cd * cda
        if cosc <= -0.95:
            return None
        k = 2 / (1 + cosc)
        return k * cd * math.sin(da), k * (self.cd0 * sd - self.sd0 * cd * cda)

    @staticmethod
    def radius_for_angle(c_deg):
        """中心から角距離 c（度）の点の投影半径"""
        return 2 * math.tan(c_deg * D2R / 2)


# ----------------------------------------------------------------------------
# 書式化
# ----------------------------------------------------------------------------

def format_ra(ra_deg, sec_decimals=1):
    """赤経（度）→ "05h 35m 17.3s" """
    h = norm_ra(ra_deg) / 15
    f = 10 ** sec_decimals
    total = round(h * 3600 * f) / f
    if total >= 86400:
        total -= 86400
    hh = int(total // 3600)
    mm = int((total - hh * 3600) // 60)
    ss = total - hh * 3600 - mm * 60
    ss_str = "%.*f" % (sec_decimals, ss)
    if ss < 10:
        ss_str = "0" + ss_str
    return "%02dh %02dm %ss" % (hh, mm, ss_str)


def format_dec(dec_deg, sec_decimals=0):
    """赤緯（度）→ "−05° 23′ 28″" """
    sign = "−" if dec_deg < 0 else "+"
    f = 10 ** sec_decimals
    total = round(abs(dec_deg) * 3600 * f) / f
    dd = int(total // 3600)
    mm = int((total - dd * 3600) // 60)
    ss = total - dd * 3600 - mm * 60
    ss_str = "%.*f" % (sec_decimals, ss)
    if ss < 10:
        ss_str = "0" + ss_str
    return "%s%02d° %02d′ %s″" % (sign, dd, mm, ss_str)


def format_angle(deg):
    """角度（度）を読みやすい単位で"""
    if deg >= 1:
        return ("%.1f°" if deg >= 10 else "%.2f°") % deg
    am = deg * 60
    if am >= 1:
        return ("%.1f′" if am >= 10 else "%.2f′") % am
    return "%.1f″" % (deg * 3600)


def format_ra_short(ra_deg):
    """星図のグリッド用: 赤経（度）→ "5h30m" 形式"""
    total = int(round(norm_ra(ra_deg) / 15 * 60))
    if total >= 1440:
        total -= 1440
    h, m = divmod(total, 60)
    return "%dh" % h if m == 0 else "%dh%02dm" % (h, m)


def format_dec_short(dec_deg):
    total = int(round(dec_deg * 60))
    if total == 0:
        return "0°"
    sign = "−" if total < 0 else "+"
    d, m = divmod(abs(total), 60)
    return "%s%d°" % (sign, d) if m == 0 else "%s%d°%02d′" % (sign, d, m)


def parse_datetime(v):
    """datetime または ISO 8601 文字列 → UTC の datetime。失敗時 None。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        dt = v
    else:
        s = str(v).strip().strip("'").strip()
        if not s:
            return None
        s = s.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            # 小数秒の桁数が多い場合など
            try:
                dt = datetime.fromisoformat(s[:19])
            except ValueError:
                return None
    # タイムゾーン指定が無い FITS の日時は UTC とみなす
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    if dt.year < 1900:
        return None
    return dt


def format_datetime(dt, with_time=True):
    if not dt:
        return ""
    s = "%04d-%02d-%02d" % (dt.year, dt.month, dt.day)
    if with_time:
        s += " %02d:%02d UTC" % (dt.hour, dt.minute)
    return s


def format_duration(sec):
    """秒 → "3h 20m" / "5m 30s" / "300s" """
    if not sec or sec <= 0:
        return ""
    if sec < 60:
        v = round(sec * 10) / 10
        return ("%d" % v if v == int(v) else "%.1f" % v) + "s"
    total = int(round(sec))
    h = total // 3600
    m = (total - h * 3600) // 60
    s = total - h * 3600 - m * 60
    if h > 0:
        return "%dh %02dm" % (h, m)
    return "%dm" % m + (" %02ds" % s if s > 0 else "")


def nice_step(span, target, candidates):
    """「きりの良い」刻み幅: span/step がおよそ target 本になるもの"""
    for c in candidates:
        if span / c <= target:
            return c
    return candidates[-1]


# 赤緯グリッドの候補（度）
DEC_STEPS = [1 / 60, 2 / 60, 5 / 60, 10 / 60, 15 / 60, 20 / 60, 30 / 60, 1, 2, 5, 10, 15, 20, 30]
# 赤経グリッドの候補（度。時間単位 1m = 0.25°）
RA_STEPS = [5 / 240, 10 / 240, 15 / 240, 30 / 240, 1 / 4, 2 / 4, 5 / 4, 10 / 4, 15 / 4, 20 / 4, 30 / 4, 15, 30, 45]


def parse_sexagesimal(s):
    """"+35 30 00" / "35:30:00" / "35.5" → 35.5"""
    if s is None:
        return None
    s = str(s).strip().strip("'").strip()
    if not s:
        return None
    parts = [p for p in s.replace(":", " ").split() if p]
    try:
        if len(parts) == 1:
            v = float(parts[0])
            return v if math.isfinite(v) else None
        neg = parts[0].startswith("-")
        v = 0.0
        f = 1.0
        for p in parts[:3]:
            v += abs(float(p)) / f
            f *= 60
        return -v if neg else v
    except ValueError:
        return None
