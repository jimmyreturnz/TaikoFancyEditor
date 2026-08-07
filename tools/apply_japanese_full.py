from __future__ import annotations
import re, xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; TS=ROOT/'translations'/'taiko_ja.ts'; MISSING=ROOT/'translations'/'missing_ja_strings.txt'
JA={
'Open .osu':'.osu を開く','Play':'再生','Pause':'一時停止','Reset applied transforms':'適用した変形をリセット','Export applied map':'適用済み譜面をエクスポート','Difficulty':'難易度','Playback Rate':'再生速度','Background Opacity':'背景の不透明度','Transformation mode':'変形モード','All Notes':'すべてのノーツ','Split Don / Kat':'ドン / カッを分割','Swap Don ↔ Kat':'ドン ↔ カッを入れ替え','Transform selected notes':'選択したノーツを変形','Apply all changes to original file':'すべての変更を元のファイルに適用','Beat snap':'ビートスナップ','Duration':'曲の長さ','Now':'現在','Snap':'スナップ','Wheel: seek':'ホイール: シーク','Shift+wheel: 1 beat':'Shift+ホイール: 1拍','Ctrl+wheel: zoom':'Ctrl+ホイール: ズーム','All':'すべて','Don':'ドン','Kat':'カッ','None':'なし','Position X':'X位置','Position Y':'Y位置','Traversal':'進行方法','Font':'フォント','Random Seed':'ランダムシード','Column':'列','Columns':'列数','Min BPM':'最小BPM','Max BPM':'最大BPM','Minimum BPM':'最小BPM','Maximum BPM':'最大BPM','Step Size':'ステップ幅','Chunk':'チャンク','Chunk Size':'チャンクサイズ','Notes per Chunk':'チャンクあたりのノーツ数','Max Turn':'最大旋回角','Maximum Turn':'最大旋回角','Open Drawing Window':'描画ウィンドウを開く','Import Image...':'画像をインポート...','Undo':'元に戻す','Redo':'やり直す','Clear':'クリア','Drawing':'描画','Dark Lines':'暗い線','Light Lines':'明るい線','Alpha Outline':'アルファ輪郭','Trace Mode':'トレースモード','Threshold':'しきい値','Minimum Outline Length':'最小輪郭長','Simplification':'単純化','Invert':'反転','Refresh Preview':'プレビューを更新','Import into Drawing':'描画にインポート','Settings':'設定','General':'一般','Language':'言語','Shortcuts':'ショートカット','Advanced':'詳細設定','Direction':'方向','Rotation':'回転','Enabled':'有効','Disabled':'無効','Forward':'順方向','Reverse':'逆方向','Clockwise':'時計回り','Counterclockwise':'反時計回り','Restart Each Chunk':'チャンクごとに先頭から','Back and Forth':'往復','Top to Bottom / Left to Right':'上から下 / 左から右','Top to Bottom / Right to Left':'上から下 / 右から左','Linear':'直線','Ease In':'イーズイン','Ease Out':'イーズアウト','Text':'テキスト','Equation':'方程式','Graph Type':'グラフ形式','Graph Size (%)':'グラフサイズ (%)','Font Size':'フォントサイズ','Text Size (%)':'テキストサイズ (%)','Auto Arrange':'自動配置','Margin X':'X余白','Margin Y':'Y余白','Center X':'中心X','Center Y':'中心Y','Width':'幅','Height':'高さ','Radius':'半径','Start Angle':'開始角度','End Angle':'終了角度','Angle':'角度','Seed':'シード','Steps':'ステップ数'}
NOUN={'Drawing':'描画','Pinwheel':'ピンホイール','Line':'直線','Row':'行','Column':'列','Circle':'円','Wave':'波','Zigzag':'ジグザグ','Spiral':'らせん','Arc':'円弧','Star':'星','Triangle':'三角形','Square':'四角形','Diamond':'ひし形','Text':'テキスト','Chunk':'チャンク','Blade':'ブレード','Turn':'回転'}
def auto(s):
 m=re.fullmatch(r'Notes per (.+)',s)
 if m:return f'{NOUN.get(m.group(1),m.group(1))}あたりのノーツ数'
 m=re.fullmatch(r'(.+) Notes',s)
 if m:return f'{NOUN.get(m.group(1),m.group(1))}のノーツ数'
 return None
def main():
 tree=ET.parse(TS); missing=[]; count=0
 for c in tree.getroot().findall('context'):
  for m in c.findall('message'):
   src=m.findtext('source') or ''; tr=m.find('translation')
   if tr is None: tr=ET.SubElement(m,'translation')
   value=JA.get(src) or auto(src)
   if value: tr.text=value; tr.attrib.pop('type',None); count+=1
   elif not (tr.text or '').strip() and src: missing.append(src)
 ET.indent(tree,space='    '); tree.write(TS,encoding='utf-8',xml_declaration=True); MISSING.write_text('\n'.join(sorted(set(missing),key=str.casefold)),encoding='utf-8'); print('Applied',count,'entries; unresolved',len(set(missing)))
if __name__=='__main__':main()