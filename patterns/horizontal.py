PLAYFIELD_WIDTH=512
PLAYFIELD_HEIGHT=384
def horizontal(selected_note_indexes,line_count,notes_per_line,margin_x,margin_y):
    if line_count<1: raise ValueError("line_count must be at least 1")
    if notes_per_line<1: raise ValueError("notes_per_line must be at least 1")
    if not 0<=margin_x<256: raise ValueError("margin_x must satisfy 0 <= margin_x < 256")
    if not 0<=margin_y<192: raise ValueError("margin_y must satisfy 0 <= margin_y < 192")
    if len(selected_note_indexes)!=len(set(selected_note_indexes)): raise ValueError("selected_note_indexes must not contain duplicates")
    ordered=sorted(selected_note_indexes); capacity=line_count*notes_per_line
    if len(ordered)>capacity: raise ValueError(f"Selection has {len(ordered)} notes, but capacity is {capacity}")
    if not ordered:return {}
    ys=[192] if line_count==1 else [margin_y+i*((384-2*margin_y)/(line_count-1)) for i in range(line_count)]
    result={}
    for row in range(line_count):
        group=ordered[row*notes_per_line:(row+1)*notes_per_line]
        if not group:break
        xs=[256] if len(group)==1 else [margin_x+i*((512-2*margin_x)/(len(group)-1)) for i in range(len(group))]
        for idx,x in zip(group,xs):result[idx]=(round(x),round(ys[row]))
    return result