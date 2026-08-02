from dataclasses import dataclass
from pathlib import Path
from model.hit_object import HitObject
@dataclass(slots=True)
class OsuDocument:
    source_path:Path; lines:list[str]; encoding:str; version:str; audio_filename:str; hit_objects:list[HitObject]
def _decode_file(raw):
    if raw.startswith(b"\xef\xbb\xbf"): return raw.decode("utf-8-sig"),"utf-8-sig"
    try:return raw.decode("utf-8"),"utf-8"
    except UnicodeDecodeError:return raw.decode("cp1252"),"cp1252"
def parse_osu(path):
    source_path=Path(path).resolve(); text,encoding=_decode_file(source_path.read_bytes()); lines=text.splitlines(keepends=True)
    section=version=audio_filename=""; hit_objects=[]
    for line_index,line in enumerate(lines):
        content=line.rstrip("\r\n"); stripped=content.strip()
        if stripped.startswith("[") and stripped.endswith("]"): section=stripped[1:-1]; continue
        if not stripped or stripped.startswith("//"): continue
        if section=="General" and content.startswith("AudioFilename:"): audio_filename=content.split(":",1)[1].strip(); continue
        if section=="Metadata" and content.startswith("Version:"): version=content.split(":",1)[1].strip(); continue
        if section!="HitObjects": continue
        fields=content.split(",")
        if len(fields)<5: continue
        try: hit_objects.append(HitObject(int(fields[0]),int(fields[1]),int(fields[2]),int(fields[3]),int(fields[4]),fields[5] if len(fields)>5 else "",len(hit_objects),line_index))
        except ValueError: continue
    return OsuDocument(source_path,lines,encoding,version,audio_filename,hit_objects)