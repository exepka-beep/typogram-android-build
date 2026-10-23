import re, zipfile, sys
apk = sys.argv[1]
with zipfile.ZipFile(apk) as z:
    for name in z.namelist():
        if not re.fullmatch(r"classes\d+\.dex", name):
            continue
        data = z.read(name)
        for needle in (b"94.156.170.106", b"shapitogram.xyz", b"OwpengramServers",
                       b"DEFAULT_HOST", b"OWPENGRAM_RSA_KEY"):
            for m in re.finditer(re.escape(needle), data):
                start = max(0, m.start() - 70)
                end = min(len(data), m.end() + 90)
                chunk = data[start:end]
                text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                print("%s  @%d  %s" % (name, m.start(), needle.decode()))
                print("    %s" % text)
                print()
