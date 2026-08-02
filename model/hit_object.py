from dataclasses import dataclass
HITSOUND_WHISTLE=2
HITSOUND_FINISH=4
HITSOUND_CLAP=8
KAT_HITSOUNDS=HITSOUND_WHISTLE|HITSOUND_CLAP
@dataclass(slots=True)
class HitObject:
    x:int; y:int; time:int; type:int; hit_sound:int; hit_sample:str; original_index:int; source_line_index:int
    @property
    def is_kat(self): return bool(self.hit_sound & KAT_HITSOUNDS)
    @property
    def is_finisher(self): return bool(self.hit_sound & HITSOUND_FINISH)
    @property
    def note_kind(self):
        return ("big_kat" if self.is_finisher else "kat") if self.is_kat else ("big_don" if self.is_finisher else "don")