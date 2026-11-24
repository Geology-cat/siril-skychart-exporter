# -*- coding: utf-8 -*-
"""ページの割付と描画、PDF の書き出し。

写真以外（注記・星図・文字）はすべてベクターで描くので、PDF のサイズは実質的に
写真の画素数だけで決まる。描画は QPainter を介して行うため、同じコードで PDF
（QPdfWriter）とプレビュー（QImage）の両方に描ける（座標は mm → 装置の画素に換算）。

PixInsight 版 SCE-Render.jsh の移植。PixInsight の Graphics / PDF / Bitmap の
代わりに PyQt6 の QPainter / QPdfWriter / QImage を使う。
"""

import math
import os
import tempfile
from datetime import datetime

import numpy as np
from PyQt6.QtCore import Qt, QPointF, QRectF, QSizeF, QMarginsF
from PyQt6.QtGui import (QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QPainter, QPen, QBrush,
                         QPdfWriter, QPageSize, QPageLayout, QPolygonF)

from . import __version__
from .astro import (ang_dist, ang_dist_np, densify, StereoProjection, nice_step, DEC_STEPS, RA_STEPS, norm_ra,
                    format_ra, format_dec, format_angle, format_ra_short, format_dec_short, format_datetime, D2R)
from .catalog import dso_designation, dso_name, star_name, constellation_name
from .model import type_group, annotations_enabled
from .options import PAGE_SIZES
from .strings import tr as _tr, fmt

THEMES = {
    "light": dict(
        page=0xFFFFFF, text=0x1E2228, sub=0x6A707A, line=0xD3D7DD, panel=0xF4F6F9, accent=0x2B5FA8,
        chartBg=0xFFFFFF, chartStar=0x15181C, chartGrid=0xC5CBD3, chartConst=0x5577BB, chartBorder=0xB9A0BE,
        chartConstName=0x56688F, chartFrame=0xD3262F, chartText=0x3A3F47, chartEdge=0x9AA1AB,
        dso=dict(galaxy=0xC2571A, nebula=0xB8262E, pn=0x0E7C86, oc=0x8A6D00, gc=0x9B2F8F, snr=0xC0336E,
                 dark=0x707070, other=0x555B63)),
    "dark": dict(
        page=0x15181D, text=0xE9EBEE, sub=0x9EA4AD, line=0x353B45, panel=0x1E232A, accent=0x7FB0FF,
        chartBg=0x0B1022, chartStar=0xF2F4F8, chartGrid=0x2C3657, chartConst=0x5D7CC9, chartBorder=0x6B5A78,
        chartConstName=0x8FA2D6, chartFrame=0xFF4B55, chartText=0xB8C0CF, chartEdge=0x4A5470,
        dso=dict(galaxy=0xFFB26B, nebula=0xFF7F86, pn=0x6EE7E7, oc=0xFFE45C, gc=0xF5A8EC, snr=0xFF9EC4,
                 dark=0xA0A0A0, other=0xC8CDD5)),
}

# 写真の上の注記色（写真は暗いので常に明るい色）
PHOTO_COLORS = dict(
    galaxy=0xFFC266, nebula=0xFF8E8E, pn=0x72EAEA, oc=0xFFE45C, gc=0xFFB8F2, snr=0xFFA3C8, dark=0xC8C8C8,
    other=0xE6E6E6, star=0xAED0FF, constLine=0x84A2FF, border=0xC9A6C9, grid=0xFFFFFF, constName=0xB3C2FF,
    compass=0xFFFFFF)

# 欧文と日本語のフォント候補。日本語は PDF の文字コピー・検索で漢字が部首文字
# （例: 大 → ⼤）にならないことを確認したものを優先する（Hiragino・Yu Gothic は化ける）
LATIN_FONTS = ["Helvetica Neue", "Helvetica", "Arial", "Segoe UI", "DejaVu Sans", "Liberation Sans", "Noto Sans"]
CJK_FONTS = ["BIZ UDPGothic", "BIZ UDGothic", "Noto Sans CJK JP", "Noto Sans JP", "Source Han Sans JP",
             "IPAexGothic", "IPAPGothic", "Meiryo", "Yu Gothic UI", "Yu Gothic", "YuGothic", "Hiragino Sans",
             "Hiragino Kaku Gothic ProN", "MS PGothic", "Arial Unicode MS"]


def resolve_font_families(preferred=""):
    try:
        available = set(QFontDatabase.families())
    except Exception:  # noqa: BLE001
        available = set()
    fams = []
    if preferred:
        fams.append(preferred)
    else:
        for f in LATIN_FONTS:
            if f in available:
                fams.append(f)
                break
    for f in CJK_FONTS:
        if f in available and f not in fams:
            fams.append(f)
            break
    return fams or ["Sans Serif"]


def qc(rgb, alpha=255):
    return QColor((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF, alpha)


def numpy_to_qimage(arr):
    """(H,W,3) または (H,W) の uint8 配列 → QImage（データは複製する）"""
    a = np.ascontiguousarray(arr)
    if a.ndim == 2:
        img = QImage(a.data, a.shape[1], a.shape[0], a.strides[0], QImage.Format.Format_Grayscale8)
    else:
        img = QImage(a.data, a.shape[1], a.shape[0], a.strides[0], QImage.Format.Format_RGB888)
    return img.copy()


class LabelPlacer:
    """ラベルの重なりを避けて配置する"""

    def __init__(self, x0, y0, x1, y1):
        self.bx0, self.by0, self.bx1, self.by1 = x0, y0, x1, y1
        self.rects = []

    def fits(self, x, y, w, h):
        if x < self.bx0 or y < self.by0 or x + w > self.bx1 or y + h > self.by1:
            return False
        for r in self.rects:
            if x < r[2] and x + w > r[0] and y < r[3] and y + h > r[1]:
                return False
        return True

    def reserve(self, x, y, w, h):
        self.rects.append((x, y, x + w, y + h))

    def place(self, w, h, ax, ay, r, pad):
        """アンカー (ax, ay) の周囲（半径 r の外側）に w×h の矩形を置く。左上座標を返す。"""
        d, e = r + pad, r * 0.72 + pad
        cands = [(ax + d, ay - h / 2), (ax - d - w, ay - h / 2), (ax + e, ay - e - h), (ax + e, ay + e),
                 (ax - w / 2, ay - d - h), (ax - w / 2, ay + d), (ax - e - w, ay - e - h), (ax - e - w, ay + e)]
        # 大きな天体（周囲に置き場が無いもの）は内側の中心付近にも置く
        if r > 3 * h:
            cands += [(ax - w / 2, ay - h / 2), (ax - w / 2, ay - h * 1.6), (ax - w / 2, ay + h * 0.6)]
        for cx, cy in cands:
            if self.fits(cx, cy, w, h):
                self.reserve(cx, cy, w, h)
                return cx, cy
        return None


def _metrics_device():
    """割付計算用の 300 dpi の描画先"""
    img = QImage(4, 4, QImage.Format.Format_RGB32)
    dpm = int(round(300 / 0.0254))
    img.setDotsPerMeterX(dpm)
    img.setDotsPerMeterY(dpm)
    return img


class Renderer:
    def __init__(self, model):
        self.model = model
        self.opts = model.opts
        self.lang = model.lang
        self.theme = THEMES.get(self.opts.theme, THEMES["light"])
        self.families = resolve_font_families(self.opts.fontFamily)
        ps = PAGE_SIZES.get(self.opts.pageSize, PAGE_SIZES["A4"])
        self.W, self.H = ps["w"], ps["h"]
        # 文字や線の太さは用紙の大きさに比例させる
        self.fs = math.sqrt((self.W * self.H) / (297 * 210))
        self._mdev = _metrics_device()
        self.dev = self._mdev
        self.dpi = 300.0
        self.k = self.dpi / 25.4
        self.layout = self.compute_layout()

    def tr(self, key):
        return _tr(key, self.lang)

    # ------------------------------------------------------------------------
    # フォントと寸法
    # ------------------------------------------------------------------------

    def font(self, pt, bold=False):
        f = QFont()
        f.setFamilies(self.families)
        f.setPointSizeF(max(1.0, pt * self.fs))
        f.setBold(bold)
        f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        return f

    def fm(self, font, device=None):
        return QFontMetricsF(font, device or self.dev)

    def width(self, font, s):
        return self.fm(font).horizontalAdvance(s)

    def px(self, mm):
        return mm * self.k

    def pen(self, rgb, width_mm, alpha=255, style=Qt.PenStyle.SolidLine):
        p = QPen(qc(rgb, alpha))
        p.setWidthF(max(0.5, self.px(width_mm * self.fs)))
        p.setStyle(style)
        p.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return p

    def text(self, g, font, rgb, x, y, s, alpha=255):
        g.setFont(font)
        g.setPen(qc(rgb, alpha))
        g.drawText(QPointF(x, y), s)

    def fit(self, font, s, max_w):
        """幅に収まるよう末尾を省略"""
        if self.width(font, s) <= max_w:
            return s
        lo, hi = 0, len(s)
        while lo < hi:
            mid = (lo + hi + 1) >> 1
            if self.width(font, s[:mid] + "…") <= max_w:
                lo = mid
            else:
                hi = mid - 1
        return s[:lo] + "…" if lo > 0 else ""

    def wrap(self, font, text, max_w):
        """折り返し（英文は単語単位、日本語は文字単位。行頭禁則の簡易処理つき）"""
        lines = []
        no_start = "、。，．,.)）」』】〕〉》！？!?ー・：；"
        for para in str(text).splitlines() or [""]:
            if not para:
                lines.append("")
                continue
            cur = ""
            for ch in para:
                nxt = cur + ch
                if cur and self.width(font, nxt) > max_w:
                    if ch in no_start:
                        # 句読点は前の行にぶら下げる
                        lines.append(nxt)
                        cur = ""
                        continue
                    sp = cur.rfind(" ")
                    if ch != " " and sp > 0 and "!" <= ch <= "~" and len(cur) - sp < 24:
                        lines.append(cur[:sp])
                        cur = cur[sp + 1:] + ch
                    else:
                        lines.append(cur)
                        cur = "" if ch == " " else ch
                else:
                    cur = nxt
            lines.append(cur)
        return lines

    # ------------------------------------------------------------------------
    # 割付（mm）
    # ------------------------------------------------------------------------

    def compute_layout(self):
        fs, W, H = self.fs, self.W, self.H
        m = 8 * fs
        header_h, footer_h, gap = 14 * fs, 5 * fs, 4 * fs
        cx0, cx1, cy0, cy1 = m, W - m, m + header_h, H - m - footer_h
        cw, ch = cx1 - cx0, cy1 - cy0

        info = self.model.info
        aspect = info.width / info.height
        has_memo = len((self.opts.memo or "").strip()) > 0
        memo_min = 26 * fs

        # 右列: 星図（上）と天体一覧（下、縦長）
        w2 = max(80 * fs, min(110 * fs, cw * 0.31))
        col1w = cw - w2 - gap
        x2 = cx0 + col1w + gap
        memo = None
        memo_in_right = False

        # 縦長の写真は「写真の右に基本データ」の方が写真を大きく取れる。両方を比べて選ぶ
        side_pw = min(ch * aspect, col1w - 62 * fs - gap)
        side_area = side_pw * (side_pw / aspect) if side_pw > 0 else 0
        below_ph = ch - gap - max(self.layout_info(col1w, 2 if col1w >= 150 * fs else 1)["height"],
                                  memo_min if has_memo else 0)
        below_pw = min(col1w, below_ph * aspect)
        below_area = below_pw * (below_pw / aspect)
        if not (side_area > below_area * 1.1):
            # 横長の写真: 写真の下に基本データ（とメモ）
            def try_layout(info_w, cols):
                lay = self.layout_info(info_w, cols)
                ph = ch - gap - max(lay["height"], memo_min if has_memo else 0)
                pw = min(col1w, ph * aspect)
                return lay, pw, pw / aspect

            if has_memo:
                lay, pw, ph = try_layout(col1w * 0.58, 1)
                if ph < ch * 0.6 and col1w >= 150 * fs:
                    # 写真が小さくなりすぎるときは、基本データを2段組みにしてメモを右列へ
                    lay, pw, ph = try_layout(col1w, 2)
                    memo_in_right = True
            else:
                lay, pw, ph = try_layout(col1w, 2 if col1w >= 150 * fs else 1)
            photo = dict(x=cx0 + (col1w - pw) / 2, y=cy0, w=pw, h=ph)
            ry = cy0 + ph + gap
            rh = cy1 - ry
            info_w = col1w * 0.58 if (has_memo and not memo_in_right) else col1w
            info_box = dict(x=cx0, y=ry, w=info_w, h=rh, lay=lay)
            if has_memo and not memo_in_right:
                memo = dict(x=cx0 + info_w + gap, y=ry, w=col1w - info_w - gap, h=rh)
        else:
            # 縦長の写真: 写真の右に基本データとメモ
            pw = side_pw
            ph = pw / aspect
            photo = dict(x=cx0, y=cy0, w=pw, h=ph)
            sx, sw = cx0 + pw + gap, col1w - pw - gap
            lay = self.layout_info(sw, 1)
            ih = min(lay["height"], ch - gap - memo_min) if has_memo else ch
            info_box = dict(x=sx, y=cy0, w=sw, h=ih, lay=lay)
            if has_memo:
                memo = dict(x=sx, y=cy0 + ih + gap, w=sw, h=ch - ih - gap)

        cs = min(w2, ch * 0.52)
        chart = dict(x=x2 + (w2 - cs) / 2, y=cy0, w=cs, h=cs)
        list_y = cy0 + cs + gap
        list_h = cy1 - list_y
        if memo_in_right:
            mh = max(memo_min, list_h * 0.32)
            memo = dict(x=x2, y=cy1 - mh, w=w2, h=mh)
            list_h -= mh + gap
        lst = dict(x=x2, y=list_y, w=w2, h=list_h)
        return dict(margin=m, header=dict(x=cx0, y=m, w=cw, h=header_h - 2 * fs),
                    footer=dict(x=cx0, y=H - m - footer_h + 1.5 * fs, w=cw, h=footer_h - 1.5 * fs),
                    photo=photo, list=lst, chart=chart, info=info_box, memo=memo, gap=gap)

    def layout_info(self, width_mm, cols):
        """基本データの段組み（値は省略せずに折り返す）。寸法は mm。"""
        fs = self.fs
        k = 300 / 25.4
        f = self.font(6.8)
        col_gap = 5 * fs
        col_w = (width_mm - (cols - 1) * col_gap) / cols
        label_w = 0.0
        for r in self.model.info_rows:
            label_w = max(label_w, self.width(f, r[0]) / k)
        label_w = min(label_w, col_w * 0.38) + 2.5 * fs
        value_w = col_w - label_w
        items = [dict(label=r[0], lines=self.wrap(f, r[1], value_w * k)) for r in self.model.info_rows]
        total = sum(len(it["lines"]) for it in items)
        per = math.ceil(total / cols)
        columns, cur, n = [], [], 0
        for it in items:
            if n > 0 and n + len(it["lines"]) > per and len(columns) < cols - 1:
                columns.append(cur)
                cur, n = [], 0
            cur.append(it)
            n += len(it["lines"])
        columns.append(cur)
        max_lines = max(sum(len(it["lines"]) for it in c) for c in columns) if columns else 0
        return dict(columns=columns, colW=col_w, colGap=col_gap, labelW=label_w,
                    height=self.section_title_mm() + max_lines * self.info_row_mm() + 1.5 * fs)

    def section_title_mm(self):
        return (8.2 * 0.3528 * 1.45 + 2.8) * self.fs

    def info_row_mm(self):
        return 6.8 * 0.3528 * 1.42 * self.fs

    def photo_size_mm(self):
        return self.layout["photo"]["w"], self.layout["photo"]["h"]

    def rect_px(self, r):
        return QRectF(self.px(r["x"]), self.px(r["y"]), self.px(r["w"]), self.px(r["h"]))

    def section_title(self, g, x, y, w, title):
        f = self.font(8.2, True)
        base = self.px(y) + self.fm(f).ascent()
        self.text(g, f, self.theme["accent"], self.px(x), base, title)
        ly = base + self.px(1.2 * self.fs)
        g.setPen(self.pen(self.theme["line"], 0.25))
        g.drawLine(QPointF(self.px(x), ly), QPointF(self.px(x + w), ly))
        return ly + self.px(1.6 * self.fs)  # 本文の開始 y (px)

    # ------------------------------------------------------------------------
    # ページ全体
    # ------------------------------------------------------------------------

    def render(self, g, dpi, photo, device):
        self.dpi = dpi
        self.k = dpi / 25.4
        self.dev = device
        g.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        g.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        g.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        g.fillRect(QRectF(0, 0, self.px(self.W), self.px(self.H)), qc(self.theme["page"]))
        self.draw_header(g)
        self.draw_photo(g, photo)
        self.draw_chart(g)
        self.draw_info(g)
        self.draw_list(g)
        if self.layout["memo"]:
            self.draw_memo(g)
        self.draw_footer(g)

    def draw_header(self, g):
        L, t, info = self.layout["header"], self.theme, self.model.info
        fT = self.font(15, True)
        fS = self.font(7.6)
        mT, mS = self.fm(fT), self.fm(fS)
        x, y = self.px(L["x"]), self.px(L["y"])

        # 右側: 撮影日・撮影者
        right = []
        if info.date_obs:
            right.append(self.tr("rDate") + "  " + format_datetime(info.date_obs))
        observer = (self.opts.observer or "").strip() or info.observer
        site = (self.opts.site or "").strip()
        who = "  ·  ".join(s for s in (observer, site) if s)
        if who:
            right.append(who)
        rw = max([mS.horizontalAdvance(s) for s in right] or [0])
        xr = self.px(L["x"] + L["w"])
        for i, s in enumerate(right):
            self.text(g, fS, t["sub"], xr - mS.horizontalAdvance(s), y + mS.ascent() + i * mS.lineSpacing() * 1.15, s)

        # 左側: タイトルと副題
        max_title_w = self.px(L["w"]) - rw - self.px(6 * self.fs)
        self.text(g, fT, t["text"], x, y + mT.ascent(), self.fit(fT, self.model.title, max_title_w))
        parts = []
        if self.model.constellation:
            parts.append(constellation_name(self.model.constellation, self.lang))
        parts.append("RA " + format_ra(info.ra, 0).replace(" ", "") + "  Dec " + format_dec(info.dec).replace(" ", ""))
        parts.append(self.tr("rFov") + " " + format_angle(info.fov_w) + " × " + format_angle(info.fov_h))
        sub = "   ·   ".join(parts)
        self.text(g, fS, t["sub"], x, y + mT.ascent() + mT.descent() + mS.ascent() + self.px(0.6 * self.fs),
                  self.fit(fS, sub, max_title_w))
        ly = self.px(L["y"] + L["h"] + 0.6 * self.fs)
        g.setPen(self.pen(t["line"], 0.35))
        g.drawLine(QPointF(x, ly), QPointF(self.px(L["x"] + L["w"]), ly))

    def draw_footer(self, g):
        L, t = self.layout["footer"], self.theme
        f = self.font(6)
        m = self.fm(f)
        y = self.px(L["y"]) + m.ascent()
        now = datetime.now()
        made = self.tr("footerMade") + " %04d-%02d-%02d" % (now.year, now.month, now.day)
        left = "SkyChartExporter for Siril " + __version__
        if self.model.software:
            left += "  ·  " + self.model.software
        left += "  ·  " + made
        if self.model.catalog.source == "stellarium":
            left += "  ·  " + self.tr("catCredit")
        self.text(g, f, t["sub"], self.px(L["x"]), y, left)
        info = self.model.info
        right = os.path.basename(info.file_path) if info.file_path else info.id
        right = self.fit(f, right, self.px(L["w"] * 0.45))
        self.text(g, f, t["sub"], self.px(L["x"] + L["w"]) - m.horizontalAdvance(right), y, right)

    # ------------------------------------------------------------------------
    # 写真とアノテーション
    # ------------------------------------------------------------------------

    def draw_photo(self, g, photo):
        r = self.rect_px(self.layout["photo"])
        g.drawImage(r, photo)
        info = self.model.info
        sx = r.width() / info.width
        self.photo_rect = r
        self.photo_scale = sx  # ページpx / 画像px
        self.photo_map = lambda x, y: QPointF(r.x() + x * sx, r.y() + y * sx)
        if annotations_enabled(self.opts):
            g.save()
            g.setClipRect(r)
            try:
                self.draw_annotations(g)
            finally:
                g.restore()
        g.setPen(self.pen(self.theme["line"], 0.3))
        g.setBrush(Qt.BrushStyle.NoBrush)
        g.drawRect(r)

    def sky_polylines_to_photo(self, polylines):
        """天球上の折れ線の配列 → ページ上の折れ線の配列（無効な点で分割）"""
        info = self.model.info
        lim = info.radius * 1.6 + 2
        flat_ra, flat_dec, index = [], [], []
        for li, pl in enumerate(polylines):
            for pi, p in enumerate(pl):
                flat_ra.append(p[0])
                flat_dec.append(p[1])
                index.append((li, pi))
        if not flat_ra:
            return []
        ra = np.array(flat_ra)
        dec = np.array(flat_dec)
        near = ang_dist_np(info.ra, info.dec, ra, dec) <= lim
        valid = [[None] * len(pl) for pl in polylines]
        sel = np.nonzero(near)[0]
        if sel.size:
            x, y = info.t.to_image(ra[sel], dec[sel])
            bra, bdec = info.t.to_sky(x, y)
            tol = max(0.02, 30 * info.scale_deg)
            ok = np.isfinite(x) & np.isfinite(y) & np.isfinite(bra)
            for j, i in enumerate(sel):
                if not ok[j]:
                    continue
                if ang_dist(bra[j], bdec[j], ra[i], dec[i]) > tol:
                    continue
                li, pi = index[i]
                valid[li][pi] = self.photo_map(float(x[j]), float(y[j]))
        out = []
        max_jump = self.px(max(self.layout["photo"]["w"], self.layout["photo"]["h"]))
        for pl in valid:
            cur = []
            for p in pl:
                if p is None or (cur and abs(p.x() - cur[-1].x()) + abs(p.y() - cur[-1].y()) > max_jump):
                    if len(cur) > 1:
                        out.append(cur)
                    cur = [] if p is None else [p]
                else:
                    cur.append(p)
            if len(cur) > 1:
                out.append(cur)
        return out

    def draw_annotations(self, g):
        opts, info, C = self.opts, self.model.info, PHOTO_COLORS
        r = self.photo_rect
        placer = LabelPlacer(r.left() + 1, r.top() + 1, r.right() - 1, r.bottom() - 1)
        label_font = self.font(opts.aLabelSize)
        label_font_b = self.font(opts.aLabelSize, True)
        shadow = max(1.0, self.px(0.12 * self.fs))
        cat = self.model.catalog
        step = max(0.05, info.radius / 15)
        g.setBrush(Qt.BrushStyle.NoBrush)

        # --- 座標グリッド ---
        if opts.aGrid:
            self.draw_photo_grid(g)

        # --- 星座境界・星座線 ---
        if opts.aConstBorders and cat.const_borders:
            lines = [densify(b["pts"], step) for b in cat.const_borders
                     if any(ang_dist(info.ra, info.dec, p[0], p[1]) < info.radius + 15 for p in b["pts"])]
            g.setPen(self.pen(C["border"], 0.25, 170, Qt.PenStyle.DashLine))
            for pl in self.sky_polylines_to_photo(lines):
                g.drawPolyline(QPolygonF(pl))
        if opts.aConstLines:
            lines = [densify(l, step) for l in cat.const_lines
                     if any(ang_dist(info.ra, info.dec, p[0], p[1]) < info.radius + 30 for p in l)]
            g.setPen(self.pen(C["constLine"], 0.3, 190))
            for pl in self.sky_polylines_to_photo(lines):
                g.drawPolyline(QPolygonF(pl))

        labels = []
        # --- 星雲・星団・銀河 ---
        for o, ex, ey in self.model.annot_dso:
            color = C.get(type_group(o["type"]), C["other"])
            line_alpha = 235 if o["rank"] <= 2 else 160
            line_w = 0.3 if o["rank"] <= 2 else 0.22
            p = self.photo_map(ex, ey)
            semi = (o.get("diam") or 0) / 120 / info.scale_deg * self.photo_scale  # 長半径（ページpx）
            g.setBrush(Qt.BrushStyle.NoBrush)
            if semi >= self.px(1.8 * self.fs):
                poly = self.sky_ellipse(o)
                if poly and len(poly) > 2:
                    g.setPen(self.pen(color, line_w, line_alpha))
                    g.drawPolygon(QPolygonF(poly))
                    ar = o.get("axisRatio")
                    rr = semi * (1 / ar if (ar and ar > 1) else 1)
                    rr = max(rr, self.px(1.2 * self.fs))
                else:
                    rr = self.px(1.6 * self.fs)
            else:
                rr = self.px(1.6 * self.fs)
                g.setPen(self.pen(color, line_w, line_alpha))
                g.drawEllipse(p, rr, rr)
            name = dso_name(o, self.lang)
            s = dso_designation(o) + (" " + name if (opts.aLabelNames and name) else "")
            # 中心が画像外の大きな天体は、ラベルの基点を画像内に寄せる
            ax = max(r.left() + self.px(3), min(r.right() - self.px(3), p.x()))
            ay = max(r.top() + self.px(3), min(r.bottom() - self.px(3), p.y()))
            if ax != p.x() or ay != p.y():
                rr = 0
            labels.append(dict(s=s, color=color, ax=ax, ay=ay, r=rr, bold=o["rank"] <= 1))
            # 小さな印の上にほかのラベルが重ならないようにする（大きな天体は対象外）
            if rr <= self.px(3 * self.fs):
                placer.reserve(p.x() - rr, p.y() - rr, rr * 2, rr * 2)

        # --- 恒星 ---
        for o, ex, ey in self.model.annot_stars:
            p = self.photo_map(ex, ey)
            rr = self.px((1.3 + max(0, 3 - o["mag"]) * 0.25) * self.fs)
            g.setPen(self.pen(C["star"], 0.25, 210))
            g.drawEllipse(p, rr, rr)
            s = (o["ja"] or o["proper"] or o["bayer"]) if self.lang == "ja" else (o["proper"] or o["bayer"])
            if o["proper"] and opts.aLabelNames and s != o["bayer"]:
                s = o["bayer"] + " " + s
            labels.append(dict(s=s, color=C["star"], ax=p.x(), ay=p.y(), r=rr, bold=False))

        # --- 星座名 ---
        if opts.aConstNames:
            fC = self.font(opts.aLabelSize * 1.3, True)
            mC = self.fm(fC)
            for c, (cra, cdec) in cat.const_centers.items():
                if ang_dist(info.ra, info.dec, cra, cdec) > info.radius * 1.2:
                    continue
                xy = info.to_image(cra, cdec)
                if not xy or xy[0] < 0 or xy[1] < 0 or xy[0] >= info.width or xy[1] >= info.height:
                    continue
                p = self.photo_map(*xy)
                s = constellation_name(c, self.lang)
                w, h = mC.horizontalAdvance(s), mC.height()
                if placer.fits(p.x() - w / 2, p.y() - h / 2, w, h):
                    placer.reserve(p.x() - w / 2, p.y() - h / 2, w, h)
                    self.text(g, fC, C["constName"], p.x() - w / 2, p.y() - h / 2 + mC.ascent(), s, 200)

        # --- ラベル ---
        pad = self.px(0.7 * self.fs)
        for lb in labels:
            f = label_font_b if lb["bold"] else label_font
            m = self.fm(f)
            w, h = m.horizontalAdvance(lb["s"]), m.height()
            pos = placer.place(w, h, lb["ax"], lb["ay"], lb["r"], pad)
            if not pos:
                continue
            base = pos[1] + m.ascent()
            self.text(g, f, 0x000000, pos[0] + shadow, base + shadow, lb["s"], 200)
            self.text(g, f, lb["color"], pos[0], base, lb["s"])

        if opts.aCompass:
            self.draw_compass(g)

    def sky_ellipse(self, o):
        """天体の楕円（長径・軸比・位置角）を天球上で作り、ページ座標の多角形にする"""
        from .astro import offset_point, R2D
        info = self.model.info
        a = (o.get("diam") or 0) / 120  # 長半径（度）
        ar = o.get("axisRatio")
        b = a * (1 / ar if (ar and ar > 1) else 1)
        pa = o.get("pa") or 0
        n = 48
        ras, decs = [], []
        for i in range(n):
            t = 2 * math.pi * i / n
            ct, st = math.cos(t), math.sin(t)
            rad = (a * b) / math.sqrt((b * ct) ** 2 + (a * st) ** 2)
            ra, dec = offset_point(o["ra"], o["dec"], pa + t * R2D, rad)
            ras.append(ra)
            decs.append(dec)
        x, y = info.t.to_image(np.array(ras), np.array(decs))
        return [self.photo_map(float(x[i]), float(y[i])) for i in range(n) if np.isfinite(x[i]) and np.isfinite(y[i])]

    def draw_photo_grid(self, g):
        info, C, r = self.model.info, PHOTO_COLORS, self.photo_rect
        span = max(info.fov_w, info.fov_h)
        dec_step = nice_step(span, 5, DEC_STEPS)
        pole_in = (90 - abs(info.dec)) < info.radius * 1.05
        dec_min = min([p[1] for p in info.bounds_rd] or [info.dec])
        dec_max = max([p[1] for p in info.bounds_rd] or [info.dec])
        if pole_in:
            ra_min, ra_max = 0.0, 360.0
            if info.dec > 0:
                dec_max = 90
            else:
                dec_min = -90
        else:
            lo = hi = 0.0
            for p in info.bounds_rd:
                d = ((p[0] - info.ra + 540) % 360) - 180
                lo, hi = min(lo, d), max(hi, d)
            ra_min, ra_max = info.ra + lo, info.ra + hi
        # 赤経の線は高緯度ほど密になるので、本数の目安を cosδ で増やす
        ra_step = nice_step(ra_max - ra_min, max(3, 5 / max(0.25, math.cos(info.dec * D2R))), RA_STEPS)
        lines, kinds, values = [], [], []
        n = 64
        d = math.ceil((dec_min - dec_step) / dec_step) * dec_step
        while d <= dec_max + dec_step:
            if abs(d) < 89.999:
                a0, a1 = ra_min - ra_step, ra_max + ra_step
                lines.append([(norm_ra(a0 + (a1 - a0) * i / n), d) for i in range(n + 1)])
                kinds.append("dec")
                values.append(d)
            d += dec_step
        a = math.ceil((ra_min - ra_step) / ra_step) * ra_step
        while a <= ra_max + ra_step:
            if pole_in and a >= 360:
                break
            d0, d1 = max(-90, dec_min - dec_step), min(90, dec_max + dec_step)
            lines.append([(norm_ra(a), d0 + (d1 - d0) * i / n) for i in range(n + 1)])
            kinds.append("ra")
            values.append(norm_ra(a))
            a += ra_step

        f = self.font(max(4.5, self.opts.aLabelSize * 0.8))
        m = self.fm(f)
        off = self.px(0.8 * self.fs)
        for i, line in enumerate(lines):
            pls = self.sky_polylines_to_photo([line])
            g.setPen(self.pen(C["grid"], 0.18, 85))
            for pl in pls:
                g.drawPolyline(QPolygonF(pl))
            # ラベル: 左端または下端との交点
            label = format_dec_short(values[i]) if kinds[i] == "dec" else format_ra_short(values[i])
            done = False
            for pl in pls:
                for j in range(len(pl) - 1):
                    p, q = pl[j], pl[j + 1]
                    if (p.x() - r.left()) * (q.x() - r.left()) < 0:
                        t = (r.left() - p.x()) / (q.x() - p.x())
                        yy = p.y() + t * (q.y() - p.y())
                        if r.top() + m.height() < yy < r.bottom() - m.height():
                            self.text(g, f, C["grid"], r.left() + off, yy - off, label, 170)
                            done = True
                            break
                    elif (p.y() - r.bottom()) * (q.y() - r.bottom()) < 0:
                        t = (r.bottom() - p.y()) / (q.y() - p.y())
                        xx = p.x() + t * (q.x() - p.x())
                        if r.left() + self.px(12) < xx < r.right() - m.horizontalAdvance(label) - off:
                            self.text(g, f, C["grid"], xx + off, r.bottom() - off, label, 170)
                            done = True
                            break
                if done:
                    break

    def draw_compass(self, g):
        info, C, r = self.model.info, PHOTO_COLORS, self.photo_rect
        cx, cy = info.width / 2, info.height / 2
        d = 0.05 * min(info.fov_w, info.fov_h)
        pn = info.to_image(info.ra, min(89.9, info.dec + d))
        pe = info.to_image(info.ra + d / max(0.05, math.cos(info.dec * D2R)), info.dec)
        L = self.px(6 * self.fs)
        ox, oy = r.left() + self.px(9 * self.fs), r.bottom() - self.px(9 * self.fs)
        f = self.font(6.5, True)
        m = self.fm(f)
        pen = self.pen(C["compass"], 0.35, 230)
        shadow_pen = self.pen(0x000000, 0.6, 140)

        def arrow(p, label):
            if not p:
                return
            vx, vy = p[0] - cx, p[1] - cy
            n = math.hypot(vx, vy)
            if not n > 0:
                return
            vx, vy = vx / n, vy / n
            ex, ey = ox + vx * L, oy + vy * L
            hx, hy, ah = -vy, vx, L * 0.22
            for pn_ in (shadow_pen, pen):
                g.setPen(pn_)
                g.drawLine(QPointF(ox, oy), QPointF(ex, ey))
                g.drawLine(QPointF(ex, ey), QPointF(ex - vx * ah + hx * ah * 0.5, ey - vy * ah + hy * ah * 0.5))
                g.drawLine(QPointF(ex, ey), QPointF(ex - vx * ah - hx * ah * 0.5, ey - vy * ah - hy * ah * 0.5))
            tx = ox + vx * (L + m.height() * 0.7) - m.horizontalAdvance(label) / 2
            ty = oy + vy * (L + m.height() * 0.7) + m.ascent() / 2 - m.descent() / 2
            self.text(g, f, 0x000000, tx + 1, ty + 1, label, 160)
            self.text(g, f, C["compass"], tx, ty, label)

        arrow(pn, "N")
        arrow(pe, "E")

        # スケールバー
        target = 0.2 * info.fov_w
        cands = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 18000, 36000, 72000]  # 秒角
        L2 = cands[0]
        for c in cands:
            if c / 3600 <= target:
                L2 = c
        length = (L2 / 3600) / info.scale_deg * self.photo_scale
        bx1 = r.right() - self.px(6 * self.fs)
        bx0 = bx1 - length
        by = r.bottom() - self.px(6 * self.fs)
        label = format_angle(L2 / 3600)
        for pn_ in (shadow_pen, pen):
            g.setPen(pn_)
            g.drawLine(QPointF(bx0, by), QPointF(bx1, by))
            g.drawLine(QPointF(bx0, by - self.px(0.9)), QPointF(bx0, by + self.px(0.9)))
            g.drawLine(QPointF(bx1, by - self.px(0.9)), QPointF(bx1, by + self.px(0.9)))
        tx = (bx0 + bx1) / 2 - m.horizontalAdvance(label) / 2
        ty = by - self.px(1.4 * self.fs)
        self.text(g, f, 0x000000, tx + 1, ty + 1, label, 160)
        self.text(g, f, C["compass"], tx, ty, label)

    # ------------------------------------------------------------------------
    # 星図
    # ------------------------------------------------------------------------

    def draw_chart(self, g):
        R, t, opts, info = self.layout["chart"], self.theme, self.opts, self.model.info
        cat = self.model.catalog
        rect = self.rect_px(R)
        fov = self.model.chart_fov
        proj = StereoProjection(info.ra, info.dec)
        half = rect.width() / 2
        kk = half / StereoProjection.radius_for_angle(fov / 2)
        cx, cy = rect.center().x(), rect.center().y()
        lim = fov * 0.75 + 2

        def xy(ra, dec):
            p = proj.project(ra, dec)
            return QPointF(cx - p[0] * kk, cy - p[1] * kk) if p else None

        def inside(p, m=0):
            return p is not None and rect.left() - m <= p.x() <= rect.right() + m and \
                rect.top() - m <= p.y() <= rect.bottom() + m

        cs = rect.width() / self.px(95 * self.fs)  # 星図の大きさによる倍率
        g.fillRect(rect, qc(t["chartBg"]))
        g.save()
        g.setClipRect(rect)
        try:
            def draw_sky_line(pts, pen):
                cur = []
                g.setPen(pen)
                for ra, dec in pts:
                    q = xy(ra, dec) if ang_dist(info.ra, info.dec, ra, dec) < lim + 10 else None
                    if q is None:
                        if len(cur) > 1:
                            g.drawPolyline(QPolygonF(cur))
                        cur = []
                    else:
                        cur.append(q)
                if len(cur) > 1:
                    g.drawPolyline(QPolygonF(cur))

            step = max(0.1, fov / 60)
            g.setBrush(Qt.BrushStyle.NoBrush)

            # --- グリッド ---
            grid_labels = []
            if opts.chartGrid:
                dec_step = nice_step(fov, 6, DEC_STEPS)
                cosd = max(0.1, math.cos(info.dec * D2R))
                ra_step = nice_step(fov / cosd, 6, RA_STEPS)
                pole_in = (90 - abs(info.dec)) < fov * 0.75
                d0, d1 = max(-90, info.dec - lim), min(90, info.dec + lim)
                ra_span = 180 if pole_in else min(180, lim / cosd)
                pen = self.pen(t["chartGrid"], 0.18)
                d = math.ceil(d0 / dec_step) * dec_step
                while d <= d1:
                    if abs(d) < 89.999:
                        n = int(math.ceil(2 * ra_span * max(0.05, math.cos(d * D2R)) / step)) + 2
                        pts = [(info.ra - ra_span + 2 * ra_span * i / n, d) for i in range(n + 1)]
                        draw_sky_line(pts, pen)
                        grid_labels.append(("dec", d, pts))
                    d += dec_step
                a0, a1 = info.ra - ra_span, info.ra + ra_span
                a = math.ceil(a0 / ra_step) * ra_step
                while a < a1 + 1e-9:
                    if pole_in and a >= a0 + 360 - 1e-9:
                        break
                    n = int(math.ceil((d1 - d0) / step)) + 2
                    pts = [(a, d0 + (d1 - d0) * i / n) for i in range(n + 1)]
                    draw_sky_line(pts, pen)
                    grid_labels.append(("ra", norm_ra(a), pts))
                    a += ra_step

            # --- 星座境界 ---
            if opts.chartConstBorders and cat.const_borders:
                pen = self.pen(t["chartBorder"], 0.22, 255, Qt.PenStyle.DashLine)
                for b in cat.const_borders:
                    if any(ang_dist(info.ra, info.dec, p[0], p[1]) < lim + 20 for p in b["pts"]):
                        draw_sky_line(densify(b["pts"], step), pen)

            # --- 星座線 ---
            if opts.chartConstLines:
                pen = self.pen(t["chartConst"], 0.32, 210)
                for l in cat.const_lines:
                    if any(ang_dist(info.ra, info.dec, p[0], p[1]) < lim + 30 for p in l):
                        draw_sky_line(densify(l, step), pen)

            placer = LabelPlacer(rect.left() + 2, rect.top() + 2, rect.right() - 2, rect.bottom() - 2)
            # 左上の方位記号と右上の注記
            placer.reserve(rect.left(), rect.top(), self.px(12 * self.fs), self.px(10 * self.fs))
            placer.reserve(rect.right() - self.px(42 * self.fs), rect.top(), self.px(42 * self.fs), self.px(4.5 * self.fs))

            # --- 撮影範囲（先に予約して、ラベルが重ならないようにする） ---
            frame = [q for q in (xy(p[0], p[1]) for p in info.bounds_rd) if q is not None]
            if frame:
                fx0 = min(p.x() for p in frame)
                fy0 = min(p.y() for p in frame)
                fx1 = max(p.x() for p in frame)
                fy1 = max(p.y() for p in frame)
            else:
                fx0 = fy0 = fx1 = fy1 = 0.0
            if len(frame) > 2:
                placer.reserve(fx0, fy0, fx1 - fx0, fy1 - fy0)

            # --- 恒星 ---
            star_lim = self.model.chart_star_limit
            star_brush = QBrush(qc(t["chartStar"]))
            bg_pen = QPen(qc(t["chartBg"]))
            bg_pen.setWidthF(max(0.5, self.px(0.12 * self.fs)))
            size_k = min(1.25, 6 / star_lim)

            def star_radius(mag):
                return min(self.px((0.2 + 0.3 * max(0, star_lim - mag) * size_k) * self.fs * min(1.4, cs)),
                           self.px(1.9 * self.fs * cs))

            star_labels = []
            label_lim = 4.0 if fov < 15 else min(2.6, star_lim - 1.5)
            g.setPen(bg_pen)
            g.setBrush(star_brush)
            for s in cat.stars:
                if s["mag"] > max(star_lim, label_lim):
                    break
                if abs(s["dec"] - info.dec) > lim or ang_dist(info.ra, info.dec, s["ra"], s["dec"]) > lim:
                    continue
                p = xy(s["ra"], s["dec"])
                if not inside(p, 5):
                    continue
                rad = star_radius(s["mag"])
                if s["mag"] <= star_lim:
                    g.drawEllipse(p, rad, rad)
                if s["mag"] <= min(2.6, star_lim - 1.5) or (fov < 15 and s["proper"] and s["mag"] <= 4.0):
                    star_labels.append((s, p, rad))
            g.setBrush(Qt.BrushStyle.NoBrush)

            # --- 星雲・星団・銀河 ---
            dso_labels = []
            if opts.chartDSO:
                dso_mag = 6 if fov >= 45 else (8 if fov >= 25 else (10 if fov >= 10 else 12))
                ras, decs = cat.dso_arrays()
                near = np.nonzero(ang_dist_np(info.ra, info.dec, ras, decs) <= lim)[0]
                for i in near:
                    o = cat.dso[i]
                    named = o["rank"] == 0 or (o["rank"] <= 1 and fov < 60) or ((o.get("ja") or o.get("en")) and fov < 25)
                    if not named and not (o.get("mag") is not None and o["mag"] <= dso_mag):
                        continue
                    p = xy(o["ra"], o["dec"])
                    if not inside(p, 3):
                        continue
                    grp = type_group(o["type"])
                    color = t["dso"].get(grp, t["dso"]["other"])
                    semi = (o.get("diam") or 0) / 120 / (fov / 2) * half
                    rad = max(self.px(0.9 * self.fs * min(1.3, cs)), min(semi, half * 0.6))
                    self.chart_symbol(g, grp, p, rad, o, color)
                    dso_labels.append((o, p, rad, color))

            # --- 撮影範囲の枠 ---
            if len(frame) > 2:
                small = max(fx1 - fx0, fy1 - fy0) < self.px(3 * self.fs)
                g.setPen(Qt.PenStyle.NoPen)
                g.setBrush(QBrush(qc(t["chartFrame"], 60)))
                g.drawPolygon(QPolygonF(frame))
                g.setBrush(Qt.BrushStyle.NoBrush)
                g.setPen(self.pen(t["chartFrame"], 0.4))
                g.drawPolygon(QPolygonF(frame))
                if small:
                    mx, my, rr = (fx0 + fx1) / 2, (fy0 + fy1) / 2, self.px(3 * self.fs)
                    g.setPen(self.pen(t["chartFrame"], 0.35))
                    g.drawEllipse(QPointF(mx, my), rr, rr)
                    g.drawLine(QPointF(mx - rr * 1.8, my), QPointF(mx - rr * 1.15, my))
                    g.drawLine(QPointF(mx + rr * 1.15, my), QPointF(mx + rr * 1.8, my))
                    g.drawLine(QPointF(mx, my - rr * 1.8), QPointF(mx, my - rr * 1.15))
                    g.drawLine(QPointF(mx, my + rr * 1.15), QPointF(mx, my + rr * 1.8))
                    placer.reserve(mx - rr * 1.8, my - rr * 1.8, rr * 3.6, rr * 3.6)

            # --- ラベル ---
            fL = self.font(5.6)
            fLB = self.font(5.6, True)
            mL, mLB = self.fm(fL), self.fm(fLB)
            pad = self.px(0.5 * self.fs)

            # 枠のラベル
            if len(frame) > 2:
                f = self.font(6.4, True)
                m = self.fm(f)
                s = self.tr("frameLabel")
                w, h = m.horizontalAdvance(s), m.height()
                mx, my = (fx0 + fx1) / 2, (fy0 + fy1) / 2
                rr = max(fx1 - fx0, fy1 - fy0) / 2 + self.px(0.6)
                pos = placer.place(w, h, mx, my, max(rr, self.px(3.5 * self.fs)), pad)
                if pos:
                    self.text(g, f, t["chartFrame"], pos[0], pos[1] + m.ascent(), s)

            # 星座名
            if opts.chartConstNames:
                f = self.font(6.4, True)
                m = self.fm(f)
                for c, (cra, cdec) in cat.const_centers.items():
                    if ang_dist(info.ra, info.dec, cra, cdec) > lim:
                        continue
                    p = xy(cra, cdec)
                    if not inside(p):
                        continue
                    s = constellation_name(c, self.lang)
                    w, h = m.horizontalAdvance(s), m.height()
                    x = max(rect.left() + 3, min(rect.right() - w - 3, p.x() - w / 2))
                    y = max(rect.top() + 3, min(rect.bottom() - h - 3, p.y() - h / 2))
                    if placer.fits(x, y, w, h):
                        placer.reserve(x, y, w, h)
                        self.text(g, f, t["chartConstName"], x, y + m.ascent(), s, 220)

            # 天体ラベル
            dso_labels.sort(key=lambda e: (e[0]["rank"], e[0]["sortMag"]))
            fcx, fcy = (fx0 + fx1) / 2, (fy0 + fy1) / 2
            fr = math.hypot(fx1 - fx0, fy1 - fy0) / 2
            for o, p, rad, color in dso_labels:
                # ラベルは主要な天体だけ（記号は描く）
                big = (o.get("diam") or 0) / 60 >= fov / 30
                bright = o.get("mag") is not None and o["mag"] <= (10 if fov < 12 else 8)
                if not (o["rank"] <= 1 or (fov < 30 and o["rank"] == 2 and (big or bright)) or (fov < 6 and o["rank"] <= 3)):
                    continue
                # 撮影範囲の中の天体は、枠の外側にラベルを付ける
                if len(frame) > 2 and fx0 <= p.x() <= fx1 and fy0 <= p.y() <= fy1:
                    p, rad = QPointF(fcx, fcy), max(fr, self.px(3.5 * self.fs))
                s = dso_designation(o)
                name = dso_name(o, self.lang)
                if name and fov <= 20 and o["rank"] <= 2:
                    s += " " + name
                f, m = (fLB, mLB) if o["rank"] == 0 else (fL, mL)
                w, h = m.horizontalAdvance(s), m.height()
                pos = placer.place(w, h, p.x(), p.y(), rad, pad)
                if not pos and s != dso_designation(o):
                    s = dso_designation(o)
                    pos = placer.place(m.horizontalAdvance(s), h, p.x(), p.y(), rad, pad)
                if pos:
                    self.text(g, f, color, pos[0], pos[1] + m.ascent(), s)
            for s_, p, rad in star_labels:
                s = star_name(s_, self.lang)
                w, h = mL.horizontalAdvance(s), mL.height()
                pos = placer.place(w, h, p.x(), p.y(), rad, pad)
                if pos:
                    self.text(g, fL, t["chartText"], pos[0], pos[1] + mL.ascent(), s)

            # グリッドの目盛り（左端・下端）
            if opts.chartGrid:
                f = self.font(5)
                m = self.fm(f)
                off = self.px(0.7 * self.fs)
                for kind, v, pts in grid_labels:
                    label = format_dec_short(v) if kind == "dec" else format_ra_short(v)
                    qs = [xy(a, b) for a, b in pts]
                    for j in range(len(qs) - 1):
                        p, q = qs[j], qs[j + 1]
                        if p is None or q is None:
                            continue
                        if kind == "dec" and (p.x() - rect.left()) * (q.x() - rect.left()) < 0:
                            yy = p.y() + (rect.left() - p.x()) / (q.x() - p.x()) * (q.y() - p.y())
                            if rect.top() + m.height() < yy < rect.bottom() - m.height() * 2:
                                self.text(g, f, t["chartText"], rect.left() + off, yy - off, label, 200)
                            break
                        if kind == "ra" and (p.y() - rect.bottom()) * (q.y() - rect.bottom()) < 0:
                            xx = p.x() + (rect.bottom() - p.y()) / (q.y() - p.y()) * (q.x() - p.x())
                            if rect.left() + self.px(10) < xx < rect.right() - m.horizontalAdvance(label) - off:
                                self.text(g, f, t["chartText"], xx + off, rect.bottom() - off, label, 200)
                            break
        finally:
            g.restore()

        # 枠と注記
        g.setBrush(Qt.BrushStyle.NoBrush)
        g.setPen(self.pen(t["chartEdge"], 0.3))
        g.drawRect(rect)
        f = self.font(5.6)
        m = self.fm(f)
        note = fmt(self.tr("chartField"), format_angle(fov)) + "   " + \
            fmt(self.tr("chartMag"), "%.1f" % self.model.chart_star_limit)
        pad = self.px(1.2 * self.fs)
        nw = m.horizontalAdvance(note)
        g.fillRect(QRectF(rect.right() - nw - 2 * pad, rect.top(), nw + 2 * pad, m.height() + pad), qc(t["chartBg"], 210))
        self.text(g, f, t["chartText"], rect.right() - nw - pad, rect.top() + pad * 0.5 + m.ascent(), note)
        # 方位（星図は北が上・東が左）
        fb = self.font(6, True)
        mb = self.fm(fb)
        ox, oy, L = rect.left() + self.px(5 * self.fs), rect.top() + self.px(6.5 * self.fs), self.px(4 * self.fs)
        g.setPen(self.pen(t["chartText"], 0.3))
        g.drawLine(QPointF(ox, oy), QPointF(ox, oy - L))
        g.drawLine(QPointF(ox, oy), QPointF(ox - L, oy))
        self.text(g, fb, t["chartText"], ox - mb.horizontalAdvance("N") / 2, oy - L - self.px(0.6), "N")
        self.text(g, fb, t["chartText"], ox - L - mb.horizontalAdvance("E") - self.px(0.6),
                  oy + mb.ascent() / 2 - mb.descent() / 2, "E")

    def chart_symbol(self, g, grp, p, rad, o, color):
        pen = self.pen(color, 0.28)
        g.setBrush(Qt.BrushStyle.NoBrush)
        if grp == "galaxy":
            ar = o.get("axisRatio")
            q = 1 / ar if (ar and ar > 1) else 0.6
            pa = o.get("pa") or 0
            # 位置角は北から東回り。星図は北が上・東が左なので画面上の角度に直す
            th = (90 + pa) * D2R
            pts = []
            for i in range(32):
                tt = 2 * math.pi * i / 32
                ex, ey = rad * math.cos(tt), rad * q * math.sin(tt)
                pts.append(QPointF(p.x() - (ex * math.cos(th) - ey * math.sin(th)),
                                   p.y() - (ex * math.sin(th) + ey * math.cos(th))))
            g.setPen(Qt.PenStyle.NoPen)
            g.setBrush(QBrush(qc(color, 70)))
            g.drawPolygon(QPolygonF(pts))
            g.setBrush(Qt.BrushStyle.NoBrush)
            g.setPen(pen)
            g.drawPolygon(QPolygonF(pts))
        elif grp == "oc":
            g.setPen(self.pen(color, 0.28, 255, Qt.PenStyle.DashLine))
            g.drawEllipse(p, rad, rad)
        elif grp == "gc":
            g.setPen(pen)
            g.drawEllipse(p, rad, rad)
            g.drawLine(QPointF(p.x() - rad, p.y()), QPointF(p.x() + rad, p.y()))
            g.drawLine(QPointF(p.x(), p.y() - rad), QPointF(p.x(), p.y() + rad))
        elif grp == "pn":
            g.setPen(pen)
            g.drawEllipse(p, rad * 0.6, rad * 0.6)
            g.drawLine(QPointF(p.x() - rad, p.y()), QPointF(p.x() - rad * 0.6, p.y()))
            g.drawLine(QPointF(p.x() + rad * 0.6, p.y()), QPointF(p.x() + rad, p.y()))
            g.drawLine(QPointF(p.x(), p.y() - rad), QPointF(p.x(), p.y() - rad * 0.6))
            g.drawLine(QPointF(p.x(), p.y() + rad * 0.6), QPointF(p.x(), p.y() + rad))
        elif grp in ("nebula", "snr"):
            r = QRectF(p.x() - rad, p.y() - rad, 2 * rad, 2 * rad)
            g.fillRect(r, qc(color, 45))
            g.setPen(pen)
            g.drawRect(r)
        else:
            g.setPen(pen)
            g.drawEllipse(p, rad, rad)

    # ------------------------------------------------------------------------
    # 基本データ・一覧・メモ
    # ------------------------------------------------------------------------

    def draw_info(self, g):
        R, t = self.layout["info"], self.theme
        lay = R["lay"]
        y0 = self.section_title(g, R["x"], R["y"], R["w"], self.tr("secInfo"))
        f = self.font(6.8)
        m = self.fm(f)
        line_h = self.px(self.info_row_mm())
        y_max = self.px(R["y"] + R["h"])
        for c, col in enumerate(lay["columns"]):
            x0 = self.px(R["x"] + c * (lay["colW"] + lay["colGap"]))
            y = y0
            for it in col:
                if y + line_h > y_max + 1:
                    break
                self.text(g, f, t["sub"], x0, y + m.ascent(), it["label"])
                for line in it["lines"]:
                    if y + line_h > y_max + 1:
                        break
                    self.text(g, f, t["text"], x0 + self.px(lay["labelW"]), y + m.ascent(), line)
                    y += line_h

    def draw_list(self, g):
        """天体一覧（縦長の列）。文字は省略せず、名前が入りきらなければ次の行に折り返す。"""
        R, t = self.layout["list"], self.theme
        items = self.model.list_items
        limit = min(self.opts.listMax if self.opts.listMax > 0 else 999, len(items))
        title = self.tr("secList") + (" (%d)" % len(items) if items else "")
        y0 = self.section_title(g, R["x"], R["y"], R["w"], title)
        fD, fN, fT = self.font(6.6, True), self.font(6.6), self.font(5.8)
        mD, mN, mT = self.fm(fD), self.fm(fN), self.fm(fT)
        if not items:
            self.text(g, fN, t["sub"], self.px(R["x"]), y0 + mN.ascent(), self.tr("noObjects"))
            return
        fs = self.fs
        line_h = self.px(6.6 * 0.3528 * 1.42 * fs)
        entry_gap = line_h * 0.18
        col_gap = self.px(5 * fs)
        cols = max(1, min(4, int((R["w"] + 5 * fs) // (78 * fs))))
        col_w = (self.px(R["w"]) - (cols - 1) * col_gap) / cols
        y_max = self.px(R["y"] + R["h"])
        mr = mN.ascent() * 0.22
        indent = mr * 2 + self.px(1.2 * fs)
        sp = self.px(1.8 * fs)
        C = t["dso"]

        def block(it):
            avail = col_w - indent
            wD, wT = mD.horizontalAdvance(it["desig"]), mT.horizontalAdvance(it["type"])
            name = it["name"] or it["alt"] or ""
            name_is_alt = not it["name"]
            wN = mN.horizontalAdvance(name) if name else 0
            if wD + (sp + wN if name else 0) + sp + wT <= avail:
                return dict(lines=1, one=True, name=name, alt=name_is_alt)
            type_own = wD + sp + wT > avail
            name_indent = self.px(3 * fs)
            name_lines = self.wrap(fN, name, avail - name_indent) if name else []
            return dict(lines=1 + (1 if type_own else 0) + len(name_lines), one=False, typeOwn=type_own,
                        nameLines=name_lines, nameIndent=name_indent, alt=name_is_alt)

        col, y, shown = 0, y0, 0
        for i in range(limit):
            it = items[i]
            b = block(it)
            h = b["lines"] * line_h
            remaining = len(items) - (i + 1)
            # 最後の列では「ほか N 天体」の1行分を残す
            need = h + (line_h if (col == cols - 1 and remaining > 0) else 0)
            if y + need > y_max:
                if col < cols - 1:
                    col += 1
                    y = y0
                    need = h + (line_h if (col == cols - 1 and remaining > 0) else 0)
                    if y + need > y_max:
                        break
                else:
                    break
            x = self.px(R["x"]) + col * (col_w + col_gap)
            base = y + mN.ascent()
            color = t["text"] if it["group"] == "star" else C.get(it["group"], C["other"])
            g.setPen(Qt.PenStyle.NoPen)
            g.setBrush(QBrush(qc(color)))
            g.drawEllipse(QPointF(x + mr, base - mN.ascent() * 0.35), mr, mr)
            g.setBrush(Qt.BrushStyle.NoBrush)
            xd = x + indent
            self.text(g, fD, t["text"], xd, base, it["desig"])
            x_type = x + col_w - mT.horizontalAdvance(it["type"])
            if b["one"]:
                if b["name"]:
                    self.text(g, fN, t["sub"] if b["alt"] else t["text"], xd + mD.horizontalAdvance(it["desig"]) + sp,
                              base, b["name"])
                self.text(g, fT, t["sub"], x_type, base, it["type"])
            else:
                ly = base
                if b["typeOwn"]:
                    ly += line_h
                self.text(g, fT, t["sub"], x_type, ly, it["type"])
                for line in b["nameLines"]:
                    ly += line_h
                    self.text(g, fN, t["sub"] if b["alt"] else t["text"], xd + b["nameIndent"], ly, line)
            y += h + entry_gap
            shown += 1
        rest = len(items) - shown
        if rest > 0:
            if y + line_h > y_max and col < cols - 1:
                col += 1
                y = y0
            x = self.px(R["x"]) + col * (col_w + col_gap)
            self.text(g, fN, t["sub"], x + indent, y + mN.ascent(), fmt(self.tr("moreObjects"), rest))

    def draw_memo(self, g):
        R, t = self.layout["memo"], self.theme
        r = self.rect_px(R)
        g.fillRect(r, qc(t["panel"]))
        pad = self.px(2 * self.fs)
        y = self.section_title(g, R["x"] + 2 * self.fs, R["y"] + 1.5 * self.fs, R["w"] - 4 * self.fs, self.tr("secMemo"))
        f = self.font(7)
        m = self.fm(f)
        line_h = m.lineSpacing() * 1.15
        lines = self.wrap(f, (self.opts.memo or "").strip(), r.width() - 2 * pad)
        max_lines = max(0, int((r.bottom() - pad * 0.5 - y) // line_h))
        if len(lines) > max_lines > 0:
            lines = lines[:max_lines]
            lines[-1] = self.fit(f, lines[-1] + "…", r.width() - 2 * pad)
        for i, line in enumerate(lines[:max_lines]):
            self.text(g, f, t["text"], r.left() + pad, y + m.ascent() + i * line_h, line)


# ----------------------------------------------------------------------------

def render_preview(model, photo, width_px):
    """プレビュー用の QImage を作る"""
    renderer = Renderer(model)
    dpi = width_px / (renderer.W / 25.4)
    w, h = int(round(renderer.W / 25.4 * dpi)), int(round(renderer.H / 25.4 * dpi))
    img = QImage(w, h, QImage.Format.Format_RGB32)
    dpm = int(round(dpi / 0.0254))
    img.setDotsPerMeterX(dpm)
    img.setDotsPerMeterY(dpm)
    img.fill(QColor(255, 255, 255))
    g = QPainter(img)
    try:
        renderer.render(g, dpi, photo, img)
    finally:
        g.end()
    return img


def _write_one(path, renderer, model, photo):
    w = QPdfWriter(path)
    w.setResolution(300)
    size = QPageSize(QSizeF(renderer.W, renderer.H), QPageSize.Unit.Millimeter, "",
                     QPageSize.SizeMatchPolicy.ExactMatch)
    w.setPageLayout(QPageLayout(size, QPageLayout.Orientation.Portrait, QMarginsF(0, 0, 0, 0),
                                QPageLayout.Unit.Millimeter))
    w.setTitle(model.title)
    w.setCreator("SkyChartExporter for Siril " + __version__)
    observer = (model.opts.observer or "").strip()
    if observer and hasattr(w, "setAuthor"):
        w.setAuthor(observer)
    g = QPainter(w)
    if not g.isActive():
        raise IOError(path)
    try:
        renderer.render(g, 300, photo, w)
    finally:
        g.end()


def write_pdf(file_path, model, photo_source, log=print):
    """PDF を書き出す。自動圧縮が有効なら、目標サイズに収まるまで写真の解像度を下げて作り直す
    （写真以外はベクターなので、サイズはほぼ写真の画素数で決まる）。
    photo_source(long_px) は指定の長辺サイズの写真 QImage を返す関数。"""
    opts = model.opts
    renderer = Renderer(model)
    pw, ph = renderer.photo_size_mm()
    long_mm = max(pw, ph)
    max_bytes = opts.maxSizeMB * 1024 * 1024
    dpi = float(opts.imageDpi)
    directory = os.path.dirname(os.path.abspath(file_path))
    result = None
    for attempt in range(1, 8):
        long_px = max(64, int(round(long_mm / 25.4 * dpi)))
        photo = photo_source(long_px)
        fd, tmp = tempfile.mkstemp(prefix="SkyChartExporter-", suffix=".pdf", dir=directory)
        os.close(fd)
        try:
            _write_one(tmp, renderer, model, photo)
            size = os.path.getsize(tmp)
            if size <= 0:
                raise IOError(tmp)
            log("SkyChartExporter: attempt %d: %d px (%.0f dpi) -> %.2f MB" %
                (attempt, max(photo.width(), photo.height()), dpi, size / 1048576))
            accept = (not opts.autoCompress) or size <= max_bytes or dpi <= 50 or attempt == 7
            if accept:
                # mkstemp のファイルは所有者のみ読み書き可なので、通常のファイルと同じ権限にする
                umask = os.umask(0)
                os.umask(umask)
                os.chmod(tmp, 0o666 & ~umask)
                os.replace(tmp, file_path)
                result = dict(size=size, dpi=dpi, photoPx=max(photo.width(), photo.height()), attempts=attempt,
                              overTarget=bool(opts.autoCompress and size > max_bytes))
                break
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        # 写真の画素数 ∝ dpi² なので、サイズ比の平方根で dpi を下げる
        factor = max(0.45, min(0.9, math.sqrt(max_bytes / size) * 0.92))
        dpi = max(50.0, round(dpi * factor))
    return result
