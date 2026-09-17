"""Paquete privado y acotado de datos/modelos. Nunca descarga codigo ni secretos."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import zipfile

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024


def allowed_asset(name: str) -> bool:
    if "\\" in name or ":" in name:
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or str(path) != name:
        return False
    if name in {"demo.json", "storage/lol_recommender.db"}:
        return True
    if re.fullmatch(r"storage/ddragon/[A-Za-z0-9_.-]+\.json", name):
        return True
    return bool(re.fullmatch(
        r"models/champions/[A-Za-z0-9_-]+/[A-Z]+/(item|live)_model_[A-Za-z0-9_.-]+\.(json|joblib)",
        name,
    ))


def extract_assets(archive: Path, destination: Path, expected_sha256: str) -> dict:
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("El paquete de demo supera 128 MiB.")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise ValueError("Configura DEMO_ASSETS_SHA256 con el hash del paquete exportado.")
    with archive.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != expected_sha256.lower():
        raise ValueError("El paquete de demo no coincide con DEMO_ASSETS_SHA256.")
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        names = [item.filename for item in entries]
        if len(entries) > 1000 or len(set(names)) != len(names):
            raise ValueError("Demasiados archivos o rutas duplicadas en el paquete.")
        if sum(item.file_size for item in entries) > MAX_EXPANDED_BYTES:
            raise ValueError("Los datos expandidos superan 256 MiB.")
        for item in entries:
            mode = item.external_attr >> 16
            target = (destination / item.filename).resolve()
            if (not allowed_asset(item.filename) or stat.S_ISLNK(mode)
                    or not target.is_relative_to(destination.resolve())):
                raise ValueError("Ruta no permitida en el paquete de demo.")
        # Validar todas las entradas antes de escribir cualquiera.
        for item in entries:
            target = destination / item.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(item) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
    manifest = json.loads((destination / "demo.json").read_text(encoding="utf-8"))
    if manifest.get("format_version") != 1:
        raise ValueError("Version de paquete de demo no soportada.")
    for key in ("champion", "tier", "game_name", "tag_line", "match_id", "puuid", "language"):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            raise ValueError(f"Falta {key} en demo.json.")
    for kind in ("item", "live"):
        folder = destination / "models" / "champions" / manifest["champion"] / manifest["tier"]
        if not folder.resolve().is_relative_to((destination / "models").resolve()):
            raise ValueError("Campeon o liga no validos.")
        if not any(folder.glob(f"{kind}_model_*.joblib")):
            raise ValueError(f"Falta el modelo {kind} para el campeon de demo.")
    if not (destination / "storage/lol_recommender.db").is_file():
        raise ValueError("Falta la base de datos de demostracion.")
    cache = destination / "storage/ddragon"
    versions = json.loads((cache / "versions.json").read_text(encoding="utf-8"))
    if not versions or not re.fullmatch(r"[0-9.]+", versions[0]):
        raise ValueError("Version de Data Dragon no valida.")
    if not re.fullmatch(r"[a-z]{2}_[A-Z]{2}", manifest["language"]):
        raise ValueError("Idioma de Data Dragon no valido.")
    for kind in ("champion", "item"):
        content = json.loads((cache / f"{kind}_{manifest['language']}_{versions[0]}.json").read_text(encoding="utf-8"))
        if not isinstance(content.get("data"), dict):
            raise ValueError("Cache de Data Dragon incompleto.")
    return manifest


def download_assets(uri: str, destination: Path) -> None:
    from google.cloud import storage
    from google.api_core.retry import Retry

    if not uri.startswith("gs://") or "/" not in uri[5:]:
        raise ValueError("DEMO_ASSETS_URI debe ser gs://bucket/paquete.zip.")
    bucket_name, object_name = uri[5:].split("/", 1)
    client = storage.Client()
    blob = client.bucket(bucket_name).blob(object_name)
    retry = Retry(deadline=60)
    blob.reload(timeout=30, retry=retry)
    if blob.size is None or blob.size > MAX_ARCHIVE_BYTES:
        raise ValueError("El paquete en GCS supera el limite de 128 MiB.")
    blob.download_to_filename(
        str(destination), if_generation_match=blob.generation,
        timeout=60, retry=retry,
    )
