#!/usr/bin/env python3
"""Inventory licenses of an extracted Ardour demo bundle's actual artifacts."""
import argparse, hashlib, json, re, subprocess
from pathlib import Path

def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
    return h.hexdigest()

def ttls(d):
    return sorted(d.glob('*.ttl'))

def licenses(d):
    vals = set()
    for p in ttls(d):
        for m in re.finditer(r'doap:license\s+([^;\n]+)', p.read_text(errors='replace')):
            vals.add(m.group(1).strip())
    return sorted(vals)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', required=True)
    ap.add_argument('--out')
    a = ap.parse_args()
    b = Path(a.bundle); lv2 = b / 'lib' / 'LV2'
    dirs = sorted(d for d in lv2.iterdir() if d.is_dir() and d.suffix == '.lv2')
    rows = []
    for d in dirs:
        lic = licenses(d)
        text = ' '.join(p.read_text(errors='replace') for p in ttls(d))
        if d.name == 'Harrison.lv2':
            cat = 'proprietary/license-gated (demo-mode strings; exclude)'
        elif any('gpl' in x.lower() for x in lic):
            cat = 'GPL family (version not stated in TTL)'
        elif any('isc' in x.lower() or 'opensource.org/licenses/isc' in x for x in lic):
            cat = 'ISC'
        else:
            cat = 'no explicit doap:license in TTL'
        rows.append({'plugin_dir': d.name, 'category': cat, 'doap_license': lic,
                     'has_harrison_license_interface': 'harrisonconsoles.com/lv2/license#interface' in text})
    gms = lv2 / 'gmsynth.lv2'
    sf2 = gms / 'GeneralUser_LV2.sf2'
    key = {}
    for name in ['gmsynth.so', 'gmsynth.ttl', 'GeneralUser_LV2.sf2']:
        p = gms / name
        if p.exists(): key[str(p.relative_to(b))] = {'sha256': sha(p), 'bytes': p.stat().st_size}
    for p in [lv2/'a-fluidsynth.lv2'/'a-fluidsynth.so', lv2/'reasonablesynth.lv2'/'reasonablesynth.so']:
        if p.exists(): key[str(p.relative_to(b))] = {'sha256': sha(p), 'bytes': p.stat().st_size}
    core = 'GPL v2.0 or any later version' if b'GPL v2.0 or any later version' in (b/'bin'/'ardour-9.8.241').read_bytes() else 'not detected in binary strings'
    out = {
        'bundle': str(b), 'lv2_dir_count': len(dirs),
        'ardour_core': {'binary': 'bin/ardour-9.8.241', 'binary_license_string': core,
                        'note': 'source tag 9.8 COPYING is GPLv2+ with plugin clarification; demo bundle carries no top-level COPYING file'},
        'plugins': rows,
        'key_artifacts': key,
        'generaluser': {
            'version': 'GeneralUser GS 2.0.2',
            'license': 'GeneralUser GS License v2.0 (custom, not OSI)',
            'terms_excerpt': [
                'You may use GeneralUser GS without restriction for your own music creation, private or commercial.',
                'Please feel free to use it in your software projects, and to modify the SoundFont bank or its packaging to suit your needs.',
                'Some samples were taken from other free banks; the author cannot be 100% sure of their provenance and warns this uncertainty may concern commercial software products.',
            ],
            'redistribution_clearance': 'not established; legal review required',
        },
        'integration_note': 'Separate-process execution of Ardour does not by itself resolve plugin/SF2 distribution terms; this inventory covers the actual extracted artifacts. The render path uses only Ardour + gmsynth + GeneralUser_LV2.sf2. Harrison.lv2 is proprietary/license-gated and is excluded from any redistributed render path.',
    }
    print(json.dumps(out, indent=1))
    if a.out: open(a.out, 'w').write(json.dumps(out, indent=1))

if __name__ == '__main__':
    main()
