"""Exporta una muestra seudonimizada y los dos modelos vigentes; no usa la red.

Ejemplo: python scripts/export_demo_assets.py --champion Diana --tier GOLD
El original SQLite se abre en modo solo lectura. Nunca copia .env ni caches de Riot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.cloud.assets import MAX_ARCHIVE_BYTES, MAX_EXPANDED_BYTES, allowed_asset
from app.data.database import SCHEMA


def pseudonym(puuid: str) -> str:
    return hashlib.sha256(puuid.encode("utf-8")).hexdigest()


def export_assets(root: Path, output: Path, champion: str, tier: str, limit: int = 500,
                  language: str = "es_MX") -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", champion) or not re.fullmatch(r"[A-Z]+", tier):
        raise ValueError("Campeon o liga no validos.")
    if not 1 <= limit <= 1000:
        raise ValueError("La muestra debe contener entre 1 y 1000 partidas.")
    if output.exists():
        raise FileExistsError("El paquete ya existe. Elige otro --output para conservarlo.")
    source_path = root / "storage/lol_recommender.db"
    model_folder = root / "models/champions" / champion / tier
    models = []
    for kind in ("item", "live"):
        candidates = [p for p in sorted(model_folder.glob(f"{kind}_model_*.json"))
                      if not p.stem.endswith("_comparison") and p.with_suffix(".joblib").is_file()]
        if not candidates:
            raise ValueError(f"Falta un modelo {kind} entrenado para {champion}/{tier}.")
        models.append(candidates[-1])

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="demo-export-", dir=output.parent) as tmp:
        stage = Path(tmp)
        database = stage / "storage/lol_recommender.db"
        database.parent.mkdir()
        source = sqlite3.connect(source_path.resolve().as_uri() + "?mode=ro", uri=True)
        target = sqlite3.connect(database)
        try:
            source.execute("BEGIN")  # Una vista consistente durante toda la exportacion.
            matches = source.execute(
                """SELECT m.matchId FROM matches m
                   WHERE EXISTS (SELECT 1 FROM participants p
                                 WHERE p.matchId=m.matchId AND p.championName=?)
                   ORDER BY EXISTS (SELECT 1 FROM timeline_minutes t WHERE t.matchId=m.matchId) DESC,
                            m.gameCreation DESC LIMIT ?""", (champion, limit),
            ).fetchall()
            match_ids = [row[0] for row in matches]
            if not match_ids:
                raise ValueError("No hay partidas historicas para ese campeon.")
            selected = source.execute(
                "SELECT puuid FROM participants WHERE matchId=? AND championName=? LIMIT 1",
                (match_ids[0], champion),
            ).fetchone()[0]
            target.executescript(SCHEMA)
            placeholders = ",".join("?" for _ in match_ids)
            for table in ("matches", "participants", "teams", "bans", "timeline_minutes"):
                columns = [row[1] for row in target.execute(f"PRAGMA table_info({table})")]
                cursor = source.execute(
                    f"SELECT {','.join(columns)} FROM {table} WHERE matchId IN ({placeholders})",
                    match_ids,
                )
                values = ",".join("?" for _ in columns)
                target.executemany(f"INSERT INTO {table} VALUES ({values})", cursor)
            players = target.execute("SELECT DISTINCT puuid FROM participants").fetchall()
            for (puuid,) in players:
                if not puuid:
                    raise ValueError("La muestra contiene un jugador sin identificador.")
                alias = pseudonym(puuid)
                target.execute(
                    "UPDATE participants SET puuid=?, riotIdGameName=?, riotIdTagline=?, summonerName=? WHERE puuid=?",
                    (f"demo-{alias}", f"Jugador-{alias[:12]}", "DEMO", f"Jugador-{alias[:12]}", puuid),
                )
            target.commit()
            target.execute("VACUUM")
        finally:
            target.close()
            source.close()

        cache = root / "storage/ddragon"
        versions = json.loads((cache / "versions.json").read_text(encoding="utf-8"))
        version = versions[0]
        static_files = ["versions.json", f"champion_{language}_{version}.json", f"item_{language}_{version}.json"]
        alias = pseudonym(selected)
        manifest = {
            "format_version": 1, "champion": champion, "tier": tier,
            "game_name": f"Jugador-{alias[:12]}", "tag_line": "DEMO",
            "puuid": f"demo-{alias}", "match_id": match_ids[0],
            "language": language, "match_count": len(match_ids),
        }
        # El ZIP final solo se publica cuando todas las fuentes son validas.
        temporary_archive = stage / "assets.zip"
        with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
            package.write(database, "storage/lol_recommender.db")
            package.writestr("demo.json", json.dumps(manifest, ensure_ascii=False))
            for filename in static_files:
                package.write(cache / filename, f"storage/ddragon/{filename}")
            for meta_path in models:
                relative = meta_path.relative_to(root).as_posix()
                if not allowed_asset(relative):
                    raise ValueError("Ruta de modelo no compatible con el paquete de demo.")
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                for field in ("trained_for", "plots", "comparison_report"):
                    meta.pop(field, None)
                package.writestr(relative, json.dumps(meta, ensure_ascii=False))
                package.write(meta_path.with_suffix(".joblib"), str(Path(relative).with_suffix(".joblib")).replace("\\", "/"))
        with zipfile.ZipFile(temporary_archive) as package:
            if sum(info.file_size for info in package.infolist()) > MAX_EXPANDED_BYTES:
                raise ValueError("Paquete demasiado grande: reduce --match-limit.")
        if temporary_archive.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ValueError("Paquete comprimido demasiado grande.")
        with temporary_archive.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        # 'xb' nunca reemplaza un paquete anterior, ni siquiera por accidente.
        import shutil
        with temporary_archive.open("rb") as source, output.open("xb") as target_file:
            shutil.copyfileobj(source, target_file)
    return digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--champion", required=True)
    parser.add_argument("--tier", default="GOLD")
    parser.add_argument("--match-limit", type=int, default=500)
    parser.add_argument("--language", default="es_MX")
    parser.add_argument("--output", type=Path, default=ROOT / "storage/cloud-export/demo-assets.zip")
    args = parser.parse_args()
    digest = export_assets(ROOT, args.output, args.champion, args.tier.upper(), args.match_limit, args.language)
    print(f"Paquete local: {args.output}")
    print(f"Tamano: {args.output.stat().st_size / 1024 / 1024:.2f} MiB")
    print(f"DEMO_ASSETS_SHA256={digest}")


if __name__ == "__main__":
    main()
