from __future__ import annotations
import ast, xml.etree.ElementTree as ET
from pathlib import Path
from i18n_manifest import CONTEXT_STRINGS,IGNORED_LITERALS
ROOT=Path(__file__).resolve().parents[1];REPORT=ROOT/'translations'/'coverage_report.txt';TS=ROOT/'translations'/'taiko_ja.ts'
FILES=('gui.py','settings_dialog.py','image_trace_dialog.py')
VISIBLE={'QLabel','QPushButton','QCheckBox','setText','setToolTip','setWindowTitle','setPlaceholderText'}
def wrapped(node):
    return isinstance(node,ast.Call) and ((isinstance(node.func,ast.Attribute) and node.func.attr=='tr') or (isinstance(node.func,ast.Name) and node.func.id in {'tr_parameter','tr_main','tr_drawing','tr_image','tr_settings'}))
def main():
    raw=[]
    for filename in FILES:
        p=ROOT/filename
        if not p.exists():continue
        tree=ast.parse(p.read_text(encoding='utf-8'),filename=str(p))
        for node in ast.walk(tree):
            if not isinstance(node,ast.Call):continue
            name=node.func.id if isinstance(node.func,ast.Name) else node.func.attr if isinstance(node.func,ast.Attribute) else ''
            args=[]
            if name in VISIBLE:args=node.args[:1]
            elif name in {'addItem','addTab'}:args=node.args[:1] # internal second arg intentionally ignored
            else:continue
            for arg in args:
                if isinstance(arg,ast.Constant) and isinstance(arg.value,str) and arg.value not in IGNORED_LITERALS and any(ch.isalpha() for ch in arg.value):raw.append(f'{filename}:{node.lineno}: {arg.value}')
                elif wrapped(arg):pass
    unfinished=[]
    if TS.exists():
        tree=ET.parse(TS)
        for c in tree.getroot().findall('context'):
            cn=c.findtext('name') or ''
            for m in c.findall('message'):
                src=m.findtext('source') or '';tr=m.find('translation')
                if tr is None or not (tr.text or '').strip() or tr.get('type')=='unfinished':unfinished.append(f'{cn}: {src}')
    lines=['RAW UNWRAPPED VISIBLE LITERALS:',*sorted(set(raw)),'','UNFINISHED REQUIRED TRANSLATIONS:',*sorted(set(unfinished))]
    REPORT.parent.mkdir(exist_ok=True);REPORT.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('Raw visible literals:',len(set(raw)),'Unfinished required translations:',len(set(unfinished)))
    return 1 if raw or unfinished else 0
if __name__=='__main__':raise SystemExit(main())