"""
DMClients build script

Requirements:
pip install requests PySocks rich python-v2ray flet[all] psutil pyinstaller winloop
"""

import os
import sys
import subprocess
import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "Out")

def patch_script(src, dst):
    with open(src, 'r', encoding='utf-8') as f:
        c = f.read()
    c = c.replace(
        'os.path.dirname(__file__)',
        'os.path.dirname(sys.executable)'
    )
    c = c.replace(
        'optimal_proxies_new.py',
        'optimal_proxies_new.exe'
    )
    c = c.replace(
        'ports_proxies.py',
        'ports_proxies.exe'
    )
    c = c.replace(
        '[sys.executable, "-u", script_path]',
        '[script_path]'
    )
    c = c.replace(
        '[sys.executable, "-u", ports_script]',
        '[ports_script]'
    )
    with open(dst, 'w', encoding='utf-8') as f:
        f.write(c)

def main():
    try:
        import PyInstaller
        print("✅ PyInstaller found")
    except ImportError:
        print("❌ pip install pyinstaller")
        return

    for folder in ['build', 'dist', OUT_DIR]:
        path = os.path.join(BASE_DIR, folder)
        if os.path.exists(path):
            shutil.rmtree(path)
            print(f"🧹 Cleaned {folder}/")

    temp_dir = os.path.join(OUT_DIR, "temp")
    os.makedirs(temp_dir, exist_ok=True)

    ui_src = os.path.join(BASE_DIR, "UI.py")
    ui_dst = os.path.join(temp_dir, "UI.py")
    patch_script(ui_src, ui_dst)

    for name in ['ports_proxies.py', 'optimal_proxies_new.py']:
        src = os.path.join(BASE_DIR, name)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(temp_dir, name))

    scripts = {
        'UI':           ui_dst,
        'ports_proxies':       os.path.join(temp_dir, 'ports_proxies.py'),
        'optimal_proxies_new': os.path.join(temp_dir, 'optimal_proxies_new.py'),
    }

    datas = []

    # .spec
    scripts_str = "{\n" + "\n".join([f"    '{k}': r'{v}'," for k, v in scripts.items()]) + "\n}"
    datas_str = "\n".join([f"    (r'{s}', r'{d}')," for s, d in datas])

    dest = os.path.join(OUT_DIR, "DMClients")

    spec = f'''# -*- mode: python ; coding: utf-8 -*-
import os

scripts = {scripts_str}

datas = [
{datas_str}
]

hiddenimports = [
    'flet', 'psutil', 'asyncio', 'threading',
    'ports_proxies', 'optimal_proxies_new',
]

compiled_apps = []

for exe_name, script_path in scripts.items():
    a = Analysis(
        [script_path],
        binaries=[],
        datas=datas,
        hiddenimports=hiddenimports,
        hookspath=[],
        hooksconfig={{}},
        runtime_hooks=[],
        excludes=[],
        noarchive=False,
        optimize=0,
    )
    pyz = PYZ(a.pure)
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=exe_name,
        contents_directory='Python',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
    )
    compiled_apps.extend([a.binaries, a.datas, exe])

coll = COLLECT(
    *compiled_apps,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=r'{dest}',
)
'''

    spec_file = os.path.join(OUT_DIR, "DMClients.spec")
    with open(spec_file, 'w', encoding='utf-8') as f:
        f.write(spec)
    print(f"📄 Created {spec_file}")

    print("🔨 Building...")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", spec_file, "--noconfirm", "--clean",
         "--distpath", OUT_DIR, "--workpath", os.path.join(OUT_DIR, "build")],
        cwd=BASE_DIR,
    )

    if result.returncode != 0:
        print("❌ Build failed!")
        return

    out = os.path.join(OUT_DIR, "DMClients")
    if not os.path.exists(out):
        print("❌ Output folder not found")
        return

    for folder in ['DDNets-19.9-win64', 'ProxiFyre', 'Settings', 'Macros', 'Scripts', 'Temp']:
        src = os.path.join(BASE_DIR, folder)
        dst = os.path.join(out, folder)
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copytree(src, dst)
            print(f"📁 Copied {folder}/")

    for file in ['xray.exe']:
        src = os.path.join(BASE_DIR, file)
        dst = os.path.join(out, file)
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy(src, dst)
            print(f"📁 Copied {file}")

    print(f"\n✅ Done: {out}")
    exe_count = sum(1 for f in os.listdir(out) if f.endswith('.exe'))
    print(f"   EXE files: {exe_count}")
    total_size = sum(
        os.path.getsize(os.path.join(dp, fn))
        for dp, _, fns in os.walk(out)
        for fn in fns
    )
    print(f"   Total size: {total_size / (1024*1024):.1f} MB")

if __name__ == "__main__":
    main()