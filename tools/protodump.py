#!/usr/bin/env python3
"""Dump embedded protobuf FileDescriptorProto blobs from a binary as .proto-like text.
No dependencies. Usage: protodump.py <binary> [name-filter]"""
import sys, re

def varint(b, i):
    r = s = 0
    while True:
        if i >= len(b): raise ValueError
        c = b[i]; i += 1
        r |= (c & 0x7f) << s; s += 7
        if not c & 0x80: return r, i
        if s > 63: raise ValueError

def fields(b):
    i, out = 0, []
    while i < len(b):
        k, i = varint(b, i); f, w = k >> 3, k & 7
        if f == 0: raise ValueError
        if w == 0: v, i = varint(b, i)
        elif w == 2:
            n, i = varint(b, i)
            if i + n > len(b): raise ValueError
            v = b[i:i+n]; i += n
        elif w == 1: v = b[i:i+8]; i += 8
        elif w == 5: v = b[i:i+4]; i += 4
        else: raise ValueError
        out.append((f, w, v))
    return out

TYPES = {1:'double',2:'float',3:'int64',4:'uint64',5:'int32',6:'fixed64',7:'fixed32',8:'bool',9:'string',12:'bytes',13:'uint32',14:'enum',15:'sfixed32',16:'sfixed64',17:'sint32',18:'sint64',11:'msg'}
LABEL = {1:'optional',2:'required',3:'repeated'}

def msg(b, ind):
    fs = fields(b); name = next((v.decode() for f,w,v in fs if f==1), '?')
    lines = [f"{ind}message {name} {{"]
    for f,w,v in fs:
        if f == 2:
            d = {ff: vv for ff,ww,vv in fields(v)}
            t = d.get(6, b'').decode() or TYPES.get(d.get(5), str(d.get(5)))
            lines.append(f"{ind}  {LABEL.get(d.get(4),'')} {t} {d.get(1,b'').decode()} = {d.get(3)};")
        elif f == 3: lines += msg(v, ind + '  ')
        elif f == 4: lines += enum(v, ind + '  ')
        elif f == 8:
            for ff,ww,vv in fields(v):
                if ff == 1: lines.append(f"{ind}  oneof {vv.decode()};")
    lines.append(ind + "}")
    return lines

def enum(b, ind):
    fs = fields(b); name = next((v.decode() for f,w,v in fs if f==1), '?')
    vals = []
    for f,w,v in fs:
        if f == 2:
            d = {ff: vv for ff,ww,vv in fields(v)}
            vals.append(f"{d.get(1,b'').decode()}={d.get(2)}")
    return [f"{ind}enum {name} {{ " + ", ".join(vals) + " }"]

data = open(sys.argv[1], 'rb').read()
flt = sys.argv[2] if len(sys.argv) > 2 else ''
seen = set()
for m in re.finditer(rb'\x0a([\x01-\x7f])([A-Za-z0-9_/.\-]+\.proto)', data):
    if m.group(1)[0] != len(m.group(2)): continue
    start = m.start()
    # grow end until the longest prefix that parses as a FileDescriptorProto
    best = None
    i = start
    try:
        while True:
            k, j = varint(data, i); f, w = k >> 3, k & 7
            if f not in range(1, 13) or w not in (0, 2): break
            if w == 0: _, j = varint(data, j)
            else:
                n, j = varint(data, j); j += n
                if j > len(data): break
            i = j; best = i
    except ValueError:
        pass
    if not best: continue
    blob = data[start:best]
    fname = m.group(2).decode()
    if fname in seen: continue
    seen.add(fname)
    try:
        fs = fields(blob)
    except ValueError:
        continue
    pkg = next((v.decode() for f,w,v in fs if f==2), '')
    text = []
    for f,w,v in fs:
        try:
            if f == 4: text += msg(v, '')
            elif f == 5: text += enum(v, '')
        except Exception:
            text.append('// <parse error>')
    body = "\n".join(text)
    if flt and flt.lower() not in body.lower() and flt.lower() not in fname.lower(): continue
    print(f"// ===== {fname} package {pkg} @0x{start:x}\n{body}\n")
