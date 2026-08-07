from __future__ import annotations
import ast, xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; TS=ROOT/'translations'/'taiko_ja.ts'; REPORT=ROOT/'translations'/'coverage_report.txt'
FILES=('gui.py','gui_draft.py','settings_dialog.py','image_trace_dialog.py')
CALLS={'QLabel','QPushButton','QCheckBox','setText','setToolTip','setWindowTitle','setPlaceholderText','addItem','addTab'}
def main():
 raw=[]
 for name in FILES:
  p=ROOT/name
  if not p.exists():continue
  tree=ast.parse(p.read_text(encoding='utf-8'),filename=str(p))
  for n in ast.walk(tree):
   if not isinstance(n,ast.Call):continue
   fn=n.func.id if isinstance(n.func,ast.Name) else n.func.attr if isinstance(n.func,ast.Attribute) else ''
   if fn not in CALLS:continue
   for arg in n.args[:2]:
    if isinstance(arg,ast.Constant) and isinstance(arg.value,str) and any(ch.isalpha() for ch in arg.value): raw.append(f'{name}:{n.lineno}: {arg.value}')
 tree=ET.parse(TS); untranslated=[]
 for c in tree.getroot().findall('context'):
  cname=c.findtext('name') or ''
  for m in c.findall('message'):
   src=m.findtext('source') or ''; tr=m.find('translation')
   if src and (tr is None or not (tr.text or '').strip() or tr.get('type')=='unfinished'): untranslated.append(f'{cname}: {src}')
 lines=['RAW USER-VISIBLE LITERALS (review/wrap with tr):',*sorted(set(raw)),'','UNTRANSLATED TS ENTRIES:',*sorted(set(untranslated))]
 REPORT.write_text('\n'.join(lines)+'\n',encoding='utf-8'); print('Raw literals:',len(set(raw)),'Untranslated entries:',len(set(untranslated)))
 if raw or untranslated: raise SystemExit(1)
if __name__=='__main__':main()