from __future__ import annotations
import ast, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'translations'/'generated_strings.py'
FIXED={'Duration','Now','Snap','Wheel: seek','Shift+wheel: 1 beat','Ctrl+wheel: zoom','Position X','Position Y','Random Seed','Traversal','Font','Notes per Drawing','All','Column','Columns','Min BPM','Max BPM','Minimum BPM','Maximum BPM','Step Size','Chunk','Chunk Size','Notes per Chunk','Max Turn','Maximum Turn'}
def collect():
    result=set(FIXED)
    for name in ('gui_draft.py','gui.py','image_trace_dialog.py','settings_dialog.py'):
        path=ROOT/name
        if not path.exists(): continue
        tree=ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node,ast.Dict): continue
            pairs={}
            for k,v in zip(node.keys,node.values):
                try:pairs[ast.literal_eval(k)]=v
                except Exception:pass
            try:
                value=ast.literal_eval(pairs.get('label'))
                if isinstance(value,str):result.add(value)
            except Exception:pass
            try:
                for item in ast.literal_eval(pairs.get('choices')):
                    if isinstance(item,(list,tuple)) and item and isinstance(item[0],str):result.add(item[0])
            except Exception:pass
    return sorted(result,key=str.casefold)
def main():
    lines=['"""Generated. Do not edit."""','from PySide6.QtCore import QCoreApplication','','def mark_generated_strings():']
    lines += [f'    QCoreApplication.translate("Parameters", {json.dumps(x,ensure_ascii=False)})' for x in collect()]
    OUT.parent.mkdir(exist_ok=True);OUT.write_text('\n'.join(lines)+'\n',encoding='utf-8');print('Generated',len(lines)-4,'markers')
if __name__=='__main__':main()