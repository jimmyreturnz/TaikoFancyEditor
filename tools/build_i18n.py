from __future__ import annotations
import ast, subprocess, sys, xml.etree.ElementTree as ET
from pathlib import Path
from i18n_manifest import CONTEXT_STRINGS
from japanese_glossary import translate
ROOT=Path(__file__).resolve().parents[1]; TRANS=ROOT/'translations'; TS=TRANS/'taiko_ja.ts'; QM=TRANS/'taiko_ja.qm'

def collect_parameter_strings():
    values=set(CONTEXT_STRINGS['Parameters'])
    for filename in ('gui_draft.py','gui.py'):
        p=ROOT/filename
        if not p.exists():continue
        tree=ast.parse(p.read_text(encoding='utf-8'),filename=str(p))
        for node in ast.walk(tree):
            if not isinstance(node,ast.Dict):continue
            pairs={}
            for k,v in zip(node.keys,node.values):
                try:pairs[ast.literal_eval(k)]=v
                except Exception:pass
            try:
                label=ast.literal_eval(pairs['label'])
                if isinstance(label,str):values.add(label)
            except Exception:pass
            try:
                for item in ast.literal_eval(pairs['choices']):
                    if isinstance(item,(tuple,list)) and item and isinstance(item[0],str):values.add(item[0])
            except Exception:pass
    return values

def write_ts():
    contexts={k:set(v) for k,v in CONTEXT_STRINGS.items()};contexts['Parameters'].update(collect_parameter_strings())
    root=ET.Element('TS',{'version':'2.1','language':'ja_JP'})
    missing=[]
    for cname in sorted(contexts):
        context=ET.SubElement(root,'context');ET.SubElement(context,'name').text=cname
        for source in sorted(contexts[cname],key=str.casefold):
            message=ET.SubElement(context,'message');ET.SubElement(message,'source').text=source
            tr=ET.SubElement(message,'translation');value=translate(source)
            if value:tr.text=value
            else:tr.set('type','unfinished');missing.append(f'{cname}: {source}')
    ET.indent(root,space='    ');TRANS.mkdir(exist_ok=True);ET.ElementTree(root).write(TS,encoding='utf-8',xml_declaration=True)
    (TRANS/'missing_ja_strings.txt').write_text('\n'.join(missing)+('\n' if missing else ''),encoding='utf-8')
    return missing

def tool(name):
    local=ROOT/'.venv'/'Scripts'/(name+'.exe')
    return str(local) if local.exists() else name

def main():
    missing=write_ts()
    if missing:
        print('Translation build stopped. Review translations/missing_ja_strings.txt')
        return 1
    subprocess.run([tool('pyside6-lrelease'),str(TS),'-qm',str(QM)],check=True)
    print('Built',QM)
    return 0
if __name__=='__main__':raise SystemExit(main())