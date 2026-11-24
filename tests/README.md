# テスト

## 単体テスト（Siril 不要）

Python 3.10 以上で実行できます。Siril 同梱カタログ（`share/siril/catalogue`）と Stellarium が見つかれば、それらを読むテストも行います（無ければ省略）。

```sh
python3 -m venv .venv
.venv/bin/pip install -r tests/requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

## 試験画像

画像はリポジトリに含めていません。次の 3 つを `tests/data/` に用意します。

```sh
mkdir -p tests/data
# 1) 合成したプレートソルブ済みの画像（M42 付近、TAN-SIP）。32 ビットのリニア画像のほか、
#    16 ビットのリニア画像（_u16）とストレッチ済み画像（_stretched_u16）も作る
.venv/bin/python tests/make_synthetic.py tests/data/m42_linear.fit
# 2) 未ソルブの画像（エラー処理の確認用）。Astrometry.net の公開サンプル（北斗七星）
curl -L -o tests/data/unsolved.jpg https://raw.githubusercontent.com/dstndstn/astrometry.net/main/demo/apod4.jpg
```

3) `tests/data/umaj_solved.fit`: 上の `unsolved.jpg` を Siril でプレートソルブして FITS で保存したもの（実写・広角・8 ビット）。Siril の「画像プレートソルバー」（`platesolve`）か [SkySolver](https://github.com/Geology-cat/siril-skysolver) で作れます。

## Siril 実機テスト

リポジトリのルートで Siril のコンソールモードから実行します（macOS の例）。

```sh
/Applications/Siril.app/Contents/MacOS/siril-cli -d "$PWD" -s tests/integration.ssf
```

ログに `SKYCHART_OK` が 6 回出て（16 ビット版はリニアが `-> autostretch`、ストレッチ済みが `-> no stretch`）、最後の未ソルブ画像では PDF が作られなければ成功です（`siril-cli` のログでは日本語が `?` になります）。出力は `tests/output/` にできるので、`pdftoppm` などで画像にして確認してください。

## Siril なしで描画だけ確かめる

```sh
.venv/bin/python tests/offline_render.py tests/data/m42_linear.fit tests/output/offline.pdf --preset detailed
.venv/bin/python tests/dialog_smoke.py tests/data/m42_linear.fit tests/output/dialog.png
```

`offline_render.py` は FITS ファイルを直接読んで PDF とプレビュー PNG を作ります。`dialog_smoke.py` はダイアログを画面外に描いて PNG に保存し、プリセットの切り替え・設定の保存・プレビュー・書き出しを確かめます。
