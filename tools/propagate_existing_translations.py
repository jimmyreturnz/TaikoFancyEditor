from __future__ import annotations
import xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; TS=ROOT/'translations'/'taiko_ja.ts'
def main():
    tree=ET.parse(TS);root=tree.getroot();known={}
    for c in root.findall('context'):
        for m in c.findall('message'):
            src=m.findtext('source') or '';tr=m.find('translation')
            if src and tr is not None and (tr.text or '').strip() and tr.get('type')!='unfinished':known.setdefault(src,tr.text)
    copied=0
    for c in root.findall('context'):
        for m in c.findall('message'):
            src=m.findtext('source') or '';tr=m.find('translation')
            if tr is None:tr=ET.SubElement(m,'translation')
            if src in known and (not (tr.text or '').strip() or tr.get('type')=='unfinished'):
                tr.text=known[src];tr.attrib.pop('type',None);copied+=1
    ET.indent(tree,space='    ');tree.write(TS,encoding='utf-8',xml_declaration=True);print('Propagated',copied,'existing translations across contexts')
if __name__=='__main__':main()