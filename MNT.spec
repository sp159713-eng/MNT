# -*- mode: python ; coding: utf-8 -*-
#
# WHAT IS AND IS NOT PACKAGED
#
# torch and tabpfn are excluded deliberately. They are only reachable through
# signals.build("nn") and signals.build("tabpfn"), both of which lost the
# bake-off recorded in config.py - lightgbm +46bp against the MLP's +23bp and
# TabPFN's -8bp - and PRODUCTION_SIGNAL is lightgbm. Bundling torch would add
# well over a gigabyte to ship two models the project measured and rejected.
#
# This is safe only because gui.py imports nothing heavy at startup: torch,
# tabpfn and even lightgbm are all imported inside methods, so the window opens
# without them. Verified before writing this - importing gui pulls in none of
# the three. If that ever changes, the packaged app stops starting rather than
# stopping at the Backtest page, so check it again before moving an import to
# module scope.
#
# The consequence, stated plainly: in a packaged build the Backtest page's
# signal picker offers only lightgbm. BacktestPage filters the list against
# what can actually be imported rather than offering a choice that raises.
#
# artifacts/ and data_cache/ are NOT bundled. config.py points them beside the
# executable when frozen, so they are the install's own writable state - a
# model retrained in the packaged app has to survive it being closed.

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = ['joblib', 'pandas', 'walkforward', 'backtest', 'metrics',
                 'auth']

for package in ('lightgbm', 'sklearn', 'yfinance', 'curl_cffi'):
    collected = collect_all(package)
    datas += collected[0]
    binaries += collected[1]
    hiddenimports += collected[2]


a = Analysis(
    ['gui.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'tabpfn', 'tensorflow', 'keras', 'tensorboard', 'jax',
              'matplotlib', 'PIL', 'h5py', 'grpc', 'IPython', 'notebook',
              'pytest'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MNT',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MNT',
)
