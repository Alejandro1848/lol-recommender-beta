# -*- mode: python ; coding: utf-8 -*-
# Spec de PyInstaller para LoL Recommender.
# Genera dist/LoLRecommender.exe con el frontend compilado incluido.
# La API key NO se empaqueta: se lee del .env junto al .exe en runtime.
#
# Uso:
#   cd frontend && npm run build && cd ..
#   pyinstaller lol_recommender.spec

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = (
    collect_submodules("sklearn")
    + collect_submodules("app")
    + ["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
       "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on"]
)

a = Analysis(
    ["main_orchestrator.py"],
    pathex=["."],
    binaries=[],
    datas=[("frontend/dist", "frontend/dist")],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PIL"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="LoLRecommender",
    debug=False,
    strip=False,
    upx=False,
    console=True,        # consola visible: muestra la URL y los logs
    icon=None,
)
