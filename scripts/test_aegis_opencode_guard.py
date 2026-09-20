#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, os, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
SCRIPT=ROOT/"scripts"/"aegis_opencode_guard.py"
INSTALL=ROOT/"scripts"/"aegis_opencode_install.sh"
spec=importlib.util.spec_from_file_location("guard",SCRIPT)
mod=importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(mod)

def main():
    assert mod.parse_version("opencode 1.18.31")== (1,18,31)
    assert mod.normalized((1,18))==(1,18,0)
    with tempfile.TemporaryDirectory() as raw:
        p=Path(raw)/"opencode"
        p.write_text("#!/usr/bin/env bash\necho 0.9.9\n",encoding="utf-8"); p.chmod(0o755)
        r=mod.inspect(str(p))
        assert r["legacy"] is True and r["modern_fallback_allowed"] is False
        p.write_text("#!/usr/bin/env bash\necho 1.18.31\n",encoding="utf-8"); p.chmod(0o755)
        r=mod.inspect(str(p))
        assert r["health"]=="healthy" and r["modern_fallback_allowed"] is True
    text=INSTALL.read_text(encoding="utf-8")
    assert "1.18.31" in text
    assert "e9312be75ed803b7415fc2aeabda1f4fe938912a39673762dc0c38c0e11ebde4" in text
    assert "d4e332f46b227448582c0d9fc75f6f826dfe95c9f751bc2011fc4d937a042be6" in text
    assert "sha256sum -c -" in text
    assert 'AEGIS_ALLOW_OPENCODE_INSTALL:-0' in text
    print("AEGIS OPENCODE GUARD TESTS PASS")
if __name__=="__main__": main()
