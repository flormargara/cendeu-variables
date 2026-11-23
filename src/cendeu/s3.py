"""Subida a S3 con verificación de tamaño (usa la CLI de AWS; no requiere boto3).

Mantiene el layout de carpetas por período: ``<dir_local>/periodo=AAAAMM/archivo`` se sube a
``s3://<bucket>/<prefijo>/periodo=AAAAMM/archivo``. Un período que ya existe se reemplaza archivo por
archivo (los cortes nuevos rectifican meses anteriores). Nunca borra objetos.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

BUCKET = "data-cendeu-raw"


def _aws(args: list[str], perfil: str) -> str:
    r = subprocess.run(["aws", *args, "--profile", perfil], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"aws {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def subir(dir_local: str | Path, prefijo: str, perfil: str = "fmargara", bucket: str = BUCKET,
          renombrar: dict[str, str] | None = None, intentos: int = 3, simular: bool = False) -> list[dict]:
    """Sube todos los archivos de ``dir_local`` bajo ``prefijo`` y devuelve una fila de control por archivo.

    ``renombrar``: {nombre_local: nombre_s3}, p. ej. {"data_0.parquet": "data.parquet"}.
    ``simular``: sólo lista lo que subiría.
    """
    base, filas = Path(dir_local), []
    muestras = [f.parent.name for f in base.rglob("_muestra.json") if json.loads(f.read_text())["pct_muestra"] < 100]
    if muestras:
        raise ValueError(f"{base} tiene variables armadas sobre una muestra ({', '.join(sorted(muestras))}): "
                         "a S3 va sólo la población completa")
    for f in sorted(p for p in base.rglob("*") if p.is_file() and not p.name.startswith(".")):
        rel = f.relative_to(base)
        nombre = (renombrar or {}).get(rel.name, rel.name)
        clave = "/".join([prefijo.strip("/"), *rel.parts[:-1], nombre])
        local = f.stat().st_size
        fila = {"archivo": str(rel), "ruta_s3": f"s3://{bucket}/{clave}", "bytes": local, "estado": "simulado"}
        if not simular:
            for intento in range(1, intentos + 1):
                try:
                    _aws(["s3", "cp", str(f), fila["ruta_s3"], "--only-show-errors"], perfil)
                    break
                except RuntimeError:
                    if intento == intentos:
                        raise
            remoto = int(_aws(["s3api", "head-object", "--bucket", bucket, "--key", clave,
                               "--query", "ContentLength", "--output", "text"], perfil))
            fila["estado"] = "ok" if remoto == local else f"tamaño distinto ({remoto})"
        filas.append(fila)
        print(f"{fila['estado']:>8}  {fila['ruta_s3']}  {local:,} bytes", flush=True)
    return filas
