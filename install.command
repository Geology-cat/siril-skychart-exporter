#!/bin/zsh
# SkyChartExporter for Siril のインストーラー（macOS）
set -eu
src="${0:A:h}"
dest="$HOME/Library/Application Support/org.siril.Siril/scripts"
stamp="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$dest"
if [[ -f "$dest/SkyChartExporter.py" ]] && ! cmp -s "$src/SkyChartExporter.py" "$dest/SkyChartExporter.py"; then
    cp -p "$dest/SkyChartExporter.py" "$dest/SkyChartExporter.py.backup-$stamp"
fi
if [[ -d "$dest/sce" ]]; then
    if ! diff -rq -x __pycache__ "$src/sce" "$dest/sce" >/dev/null 2>&1; then
        mv "$dest/sce" "$dest/sce.backup-$stamp"
    else
        rm -rf "$dest/sce"
    fi
fi
cp "$src/SkyChartExporter.py" "$dest/SkyChartExporter.py"
mkdir -p "$dest/sce"
cp "$src"/sce/*.py "$dest/sce/"
print "インストールしました: $dest/SkyChartExporter.py と $dest/sce/"
print "Sirilのコマンド欄で reloadscripts を実行するとメニューに反映されます。"
print "プレートソルブ済みの画像を開き、スクリプト → Pythonスクリプト → SkyChartExporter を実行してください。"
