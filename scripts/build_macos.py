"""Build a clean Apple Silicon application. Only explicit public resources are bundled."""
import os
import platform
from importlib import metadata
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
BUILD=ROOT/'build'/'macos'
DIST=ROOT/'dist'
APP=DIST/'Meu Dinheiro.app'


def run(*args):
    subprocess.run([str(arg) for arg in args],cwd=ROOT,check=True)


def main():
    if sys.platform!='darwin':
        raise SystemExit('Execute a compilação macOS em um Mac.')
    if platform.machine()!='arm64':
        raise SystemExit('Esta compilação é para Apple Silicon; use um ambiente Python arm64.')
    BUILD.mkdir(parents=True,exist_ok=True); DIST.mkdir(exist_ok=True)
    helper=BUILD/'touchid-helper'
    run('xcrun','swiftc','-O','-target','arm64-apple-macos26.0',ROOT/'touchid.swift','-o',helper)
    run(sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--name','meu-dinheiro-server',
        '--distpath',BUILD/'backend','--workpath',BUILD/'pyinstaller','--specpath',BUILD,
        '--paths',ROOT/'vendor',
        '--add-data',f'{ROOT / "static"}:static','--add-data',f'{ROOT / "touchid.swift"}:.',
        '--add-binary',f'{helper}:.',ROOT/'app.py')
    if APP.exists(): shutil.rmtree(APP)
    (APP/'Contents'/'MacOS').mkdir(parents=True)
    resources=APP/'Contents'/'Resources'; resources.mkdir()
    shutil.copytree(BUILD/'backend'/'meu-dinheiro-server',resources/'backend',symlinks=True)
    run('xcrun','swiftc','-O','-target','arm64-apple-macos26.0','-framework','AppKit','-framework','WebKit',ROOT/'desktop'/'Main.swift',
        '-o',APP/'Contents'/'MacOS'/'MeuDinheiro')
    info={'CFBundleName':'Meu Dinheiro','CFBundleDisplayName':'Meu Dinheiro','CFBundleExecutable':'MeuDinheiro',
        'CFBundleIdentifier':'dev.meudinheiro.desktop','CFBundlePackageType':'APPL','CFBundleShortVersionString':'0.2.0',
        'CFBundleVersion':'2','LSMinimumSystemVersion':'26.0','NSHighResolutionCapable':True,
        'NSAppTransportSecurity':{'NSAllowsLocalNetworking':True}}
    (APP/'Contents'/'Info.plist').write_bytes(plistlib.dumps(info))
    licenses=resources/'licenses'; licenses.mkdir()
    shutil.copyfile(ROOT/'vendor'/'PYPDF-LICENSE.txt',licenses/'pypdf.txt')
    for package in ('PyJWT','cryptography','cffi','pycparser','certifi'):
        distribution=metadata.distribution(package)
        for file in distribution.files or []:
            if 'license' in str(file).lower() and distribution.locate_file(file).is_file():
                folder=licenses/package; folder.mkdir(exist_ok=True)
                shutil.copyfile(distribution.locate_file(file),folder/Path(file).name)
    # Ad-hoc signing supports local use. A public release needs Developer ID + notarization.
    run('codesign','--force','--deep','--sign','-',APP)
    run('codesign','--verify','--deep','--strict',APP)
    forbidden={'.env','financas.db','local-identity.json','accounts.db','chatgpt-auth.json','pluggy-xp.json',
               'pluggy-relay.json','brapi-token'}
    assert not any(path.name in forbidden or path.suffix.lower() in ('.csv','.ofx','.xlsx','.bak')
                   for path in APP.rglob('*')), 'Private runtime data must not enter the application'
    # A read-only DMG with an Applications shortcut provides a normal drag-to-install flow.
    staging=BUILD/'dmg'; shutil.rmtree(staging,ignore_errors=True); staging.mkdir()
    shutil.copytree(APP,staging/APP.name,symlinks=True)
    os.symlink('/Applications',staging/'Applications')
    dmg=DIST/'Meu-Dinheiro-macOS-arm64.dmg'
    if dmg.exists(): dmg.unlink()
    run('hdiutil','create','-volname','Meu Dinheiro','-srcfolder',staging,'-ov','-format','UDZO',dmg)
    print(f'Aplicativo: {APP}\nInstalador local: {dmg}')


if __name__=='__main__': main()
