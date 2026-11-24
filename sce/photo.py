# -*- coding: utf-8 -*-
"""写真のストレッチと 8 ビット化。

Siril の画素配列は (チャンネル, 高さ, 幅)（モノクロは (高さ, 幅)）で、0 行目が
画面の下端に表示される。ここでは Siril の表示と同じ向き（上下反転）の
(高さ, 幅, チャンネル) の uint8 配列にそろえる。

ストレッチ:
  "auto"     … リニア画像（中央値 < 0.08）なら Siril のオートストレッチ、そうでなければそのまま
               （8 ビットの JPEG・PNG などは仕上げ済みとみなしてそのまま）
  "none"     … そのまま（ストレッチ済み画像）
  "linked"   … Siril のオートストレッチ（チャンネル連動）
  "unlinked" … Siril のオートストレッチ（チャンネル独立）
Siril のオートストレッチは sirilpy の get_image_pixeldata(preview=True) で Siril 自身に
計算させる。Siril に接続していないとき（単体テスト）は同じ式の numpy 版を使う。
"""

import numpy as np

# Siril の既定値（src/filters/mtf.c の find_*_midtones_balance_default と同じ）
SHADOWS_CLIP = -2.8
TARGET_BG = 0.25
MAD_NORM = 1.4826


def mtf(m, x):
    """中間調伝達関数（Midtones Transfer Function）"""
    x = np.asarray(x, dtype=np.float32)
    out = np.where(x <= 0, 0.0, np.where(x >= 1, 1.0, ((m - 1) * x) / ((2 * m - 1) * x - m)))
    return out.astype(np.float32)


def _mtf_params(ch_list):
    """チャンネルごとの中央値と MAD から、(シャドウ, 中間調) を求める（連動用は平均）"""
    meds, mads = [], []
    for c in ch_list:
        s = c[:: max(1, c.shape[0] // 1000), :: max(1, c.shape[1] // 1000)].astype(np.float32).ravel()
        med = float(np.median(s))
        meds.append(med)
        mads.append(float(np.median(np.abs(s - med))) * MAD_NORM)
    return meds, mads


def autostretch(data01, linked=True):
    """Siril のオートストレッチの numpy 版。data01: (C,H,W) の 0〜1 の配列。返り値も 0〜1。"""
    c = data01.shape[0]
    meds, mads = _mtf_params([data01[i] for i in range(c)])
    out = np.empty_like(data01, dtype=np.float32)
    if linked:
        med = sum(meds) / c
        mad = sum(mads) / c
        params = [_one_param(med, mad)] * c
    else:
        params = [_one_param(meds[i], mads[i]) for i in range(c)]
    for i, (shadows, midtones, highlights) in enumerate(params):
        x = (data01[i] - shadows) / max(1e-9, highlights - shadows)
        out[i] = mtf(midtones, np.clip(x, 0, 1))
    return out


def _one_param(median, mad):
    if mad == 0:
        mad = 0.001
    is_inverted = median > 0.5
    if not is_inverted:
        shadows = min(1.0, max(0.0, median + SHADOWS_CLIP * mad))
        highlights = 1.0
        x = median - shadows
        midtones = float(mtf(TARGET_BG, x)) if x > 0 else 0.5
    else:
        shadows = 0.0
        highlights = min(1.0, max(0.0, median - SHADOWS_CLIP * mad))
        x = highlights - median
        midtones = 1 - float(mtf(TARGET_BG, x)) if x > 0 else 0.5
    return shadows, midtones, highlights


def as_chw(arr):
    a = np.asarray(arr)
    if a.ndim == 2:
        a = a[np.newaxis, :, :]
    return a


def normalize(arr, orig_bitpix=None):
    """Siril の画素値 → 0〜1 の float32（(C,H,W)）"""
    a = as_chw(arr)
    if a.dtype == np.uint8:
        return a.astype(np.float32) / 255.0
    if a.dtype == np.uint16:
        # 8 ビット画像（JPEG など）は 0〜255 の値のまま 16 ビットで保持される
        div = 255.0 if orig_bitpix == 8 else 65535.0
        return np.clip(a.astype(np.float32) / div, 0, 1)
    return np.clip(np.nan_to_num(a.astype(np.float32)), 0, 1)


def to_display_uint8(chw01):
    """0〜1 の (C,H,W) → Siril の表示と同じ向きの (H,W,C) uint8（連続配列）"""
    a = np.clip(chw01 * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return flip_to_display(a)


def flip_to_display(chw_u8):
    """(C,H,W) uint8 → 上下反転した (H,W,C)。チャンネルが 1 なら (H,W)。"""
    a = chw_u8[:, ::-1, :]
    if a.shape[0] == 1:
        return np.ascontiguousarray(a[0])
    if a.shape[0] >= 3:
        return np.ascontiguousarray(np.transpose(a[:3], (1, 2, 0)))
    return np.ascontiguousarray(a[0])


def is_linear(medians01):
    """チャンネルの中央値（0〜1）からリニア画像かどうかを判定する"""
    vals = [m for m in medians01 if m is not None]
    return bool(vals) and float(np.mean(vals)) < 0.08


def render_photo_from_siril(siril, mode, log=print):
    """Siril に読み込まれている画像を、表示向きの uint8 配列にする"""
    fit = siril.get_image(with_pixels=False)
    orig_bitpix = int(getattr(fit, "orig_bitpix", 0) or 0)
    channels = int(fit.naxes[2]) if hasattr(fit, "naxes") else 1

    if mode == "auto" and orig_bitpix == 8:
        # JPEG・PNG など 8 ビットの画像は表示用に仕上げ済みとみなし、ストレッチしない
        mode = "none"
        log("SkyChartExporter: 8-bit image -> no stretch")
    if mode == "auto":
        meds = []
        for ch in range(channels):
            try:
                st = siril.get_image_stats(ch)
                med = float(st.median)
                bitpix = int(getattr(fit, "bitpix", 0) or 0)
                # 16 ビット画像の統計は 0〜65535（8 ビット由来なら 0〜255）、32 ビット浮動小数点は 0〜1
                if bitpix in (16, 20) or med > 1.5:
                    med /= (255.0 if orig_bitpix == 8 else 65535.0)
                meds.append(med)
            except Exception:  # noqa: BLE001
                pass
        if not meds:
            data = normalize(siril.get_image_pixeldata(), orig_bitpix)
            meds = [float(np.median(data[i][::8, ::8])) for i in range(data.shape[0])]
        mode = "linked_auto" if is_linear(meds) else "none"
        log("SkyChartExporter: image median %.4f -> %s" % (float(np.mean(meds)), "autostretch" if mode != "none" else "no stretch"))

    if mode in ("linked", "unlinked", "linked_auto"):
        linked = mode == "linked"
        if mode == "linked_auto":
            # 自動では Siril の表示設定（チャンネル連動）に合わせる。取得できなければ連動
            linked = True
            try:
                linked = bool(siril.get_siril_stf_linked())
            except Exception:  # noqa: BLE001
                pass
        arr = siril.get_image_pixeldata(preview=True, linked=linked)
        return flip_to_display(as_chw(np.asarray(arr, dtype=np.uint8)))
    data = normalize(siril.get_image_pixeldata(), orig_bitpix)
    return to_display_uint8(data)
