import re, io

# OLD = 上游 @generated 机翻原版（留档为 .generated.rs）
# NEW = 当前生效版（手工重译后，即 zh_cn_speech.rs）
OLD = 'zh_cn_speech.generated.rs'
NEW = 'zh_cn_speech.rs'


def keys(path):
    t = io.open(path, encoding='utf-8').read()
    body = t[t.index('Speech {'):]
    ks = []
    for ln in body.split('\n'):
        m = re.match(r'^    ([a-z_]+):', ln)
        if m:
            ks.append(m.group(1))
    return ks


a = keys(OLD)
b = keys(NEW)
print("生成版键数:", len(a))
print("当前版键数:", len(b))
print("键名与顺序完全一致:", a == b)
if a != b:
    print("  只在生成版:", [k for k in a if k not in b])
    print("  只在当前版:", [k for k in b if k not in a])
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            print("  第%d处不同: %s vs %s" % (i + 1, x, y))
            break


def phs(path):
    t = io.open(path, encoding='utf-8').read()
    out = {}
    for ln in t.split('\n'):
        m = re.match(r'^    ([a-z_]+):\s*"(.*)",$', ln)
        if m:
            out[m.group(1)] = sorted(set(re.findall(r'\{(\w+)\}', m.group(2))))
    return out


pa, pb = phs(OLD), phs(NEW)
bad = [(k, pa.get(k), pb.get(k)) for k in pa if k in pb and pa[k] != pb[k]]
print("")
print("占位符不一致的键:", len(bad))
for k, x, y in bad:
    print("  %-22s 原=%s 新=%s" % (k, x, y))


def vals(path):
    t = io.open(path, encoding='utf-8').read()
    out = {}
    for ln in t.split('\n'):
        m = re.match(r'^    ([a-z_]+):\s*"(.*)",$', ln)
        if m:
            out[m.group(1)] = m.group(2)
    return out


va, vb = vals(OLD), vals(NEW)
changed = [k for k in va if k in vb and va[k] != vb[k]]
print("")
print("话术条目改动数:", len(changed), "/", len(va))
