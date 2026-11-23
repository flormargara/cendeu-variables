"""Actualización mensual: de los archivos bajados de ARCA a la base, las variables y S3, con controles.

La descarga queda manual (ARCA pide clave fiscal y aceptar la DJ de la Ley 25.326). Todo lo demás corre con
``cendeu actualizar``:

    detectar    ¿están los seis paquetes del corte en la carpeta de entrada? (sirve para una revisión programada)
    convertir   24DSF → Parquet + control contra 24DSF_control.txt; paquete mensual y padrón → Parquet
    ipc         IPC INDEC hasta el corte (imputa los meses sin publicar y lo avisa)
    controles   compara el corte nuevo con el anterior; con un error frena antes de tocar nada
    promover    reemplaza en la carpeta de datos los meses del corte anterior (los viejos quedan en _actualizaciones)
    base        base v2 nueva (en el mes de corte el mensual completa al 24DSF)
    variables   fechas nuevas: el corte (foto actual, sin target) y corte-6 (primera con desempeño completo)
    monitoreo   PSI de cada variable de las fechas nuevas contra la última fecha que ya existía
    subir       S3 (deudores, fuentes, base, variables) y registro en _control/
    resumen     reportes/actualizaciones/AAAAMM.md

Cada paso deja su resultado en ``<datos>/_actualizaciones/AAAAMM/estado.json``; una corrida cortada retoma
desde el primer paso sin terminar. Todo lo nuevo se arma primero en esa carpeta y se mueve a su lugar sólo
después de los controles.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import math
import shutil
import subprocess
import time
from pathlib import Path

import duckdb

from . import catalogo as cat_mod
from . import paquete, pipeline, s3, v2
from .pipeline import periodo_mas

PASOS = ["convertir", "ipc", "controles", "promover", "base", "variables", "monitoreo", "subir", "resumen"]
DESEMPENO = 6

# Umbrales de los controles (proporción de cambio)
TOL_SOLAPADO_ALERTA, TOL_SOLAPADO_ERROR = 0.01, 0.10   # meses que ya estaban en el corte anterior
TOL_MES_NUEVO_ALERTA, TOL_MES_NUEVO_ERROR = 0.05, 0.20  # mes nuevo contra el anterior
PSI_ALERTA = 0.1


def _ahora() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _q(path) -> str:
    return str(path).replace("'", "''")


# --------------------------------------------------------------------------- detectar
def paquetes(corte: int) -> dict[str, str]:
    """Nombre de cada paquete de ARCA para el corte (el padrón tiene fecha propia: ``AAAAMMDDPADRON``)."""
    return {"24dsf": f"24DSF{corte}", "mensual": f"{corte}DEUDORES", "1dsf": f"1DSF{corte}",
            "inf_ret": f"{corte}INFRET", "inf_retpa": f"{corte}INFRETPA", "padron": "PADRON"}


def _buscar(entrada: Path, nombre: str) -> Path | None:
    """Carpeta o archivo comprimido (.7z, .zip) con ese nombre, sin distinguir mayúsculas (primero la carpeta,
    para no volver a descomprimir lo que ya está descomprimido)."""
    for p in sorted(entrada.iterdir(), key=lambda x: (not x.is_dir(), x.name)) if entrada.exists() else []:
        base = p.name.upper()
        for ext in (".7Z", ".ZIP"):
            base = base.removesuffix(ext)
        if base == nombre.upper():
            return p
    return None


def _buscar_padron(entrada: Path, corte: int) -> tuple[Path | None, int | None]:
    """El padrón más reciente publicado después del mes de corte (``AAAAMMDDPADRON``)."""
    cands = []
    for p in entrada.iterdir() if entrada.exists() else []:
        n = p.name.upper().removesuffix(".7Z").removesuffix(".ZIP")
        if n.endswith("PADRON") and n[:8].isdigit() and int(n[:6]) > corte:
            cands.append((int(n[:8]), p.is_dir(), p))
    if not cands:
        return None, None
    fecha, _, p = max(cands)
    return p, fecha


def detectar(entrada: str | Path, corte: int) -> dict:
    """``{"completo": bool, "faltan": [...], "encontrados": {clave: ruta}, "padron": AAAAMMDD}``."""
    entrada = Path(entrada)
    encontrados, faltan = {}, []
    for clave, nombre in paquetes(corte).items():
        if clave == "padron":
            p, fecha = _buscar_padron(entrada, corte)
        else:
            p, fecha = _buscar(entrada, nombre), None
        if p is None:
            faltan.append(nombre if clave != "padron" else f"AAAAMMDDPADRON posterior a {corte}")
        else:
            encontrados[clave] = str(p)
    return {"corte": corte, "completo": not faltan, "faltan": faltan, "encontrados": encontrados,
            "padron": _buscar_padron(entrada, corte)[1]}


def proximo_corte(datos: str | Path) -> int:
    """El mes siguiente al último período de deudores que hay en la carpeta de datos."""
    periodos = [int(p.name.split("=")[1]) for p in (Path(datos) / "deudores_24m").glob("periodo=*")]
    if not periodos:
        raise ValueError(f"{datos}/deudores_24m no tiene períodos: indicar --corte")
    return periodo_mas(max(periodos), 1)


def sincronizar_entrada(origen: str, destino: Path, perfil: str = "fmargara") -> Path:
    """Si la entrada es ``s3://…``, la copia a ``destino`` (sólo lo que falta)."""
    if not origen.startswith("s3://"):
        return Path(origen).expanduser()
    destino.mkdir(parents=True, exist_ok=True)
    subprocess.run(["aws", "s3", "sync", origen, str(destino), "--only-show-errors", "--profile", perfil], check=True)
    return destino


def _extraer(p: Path, destino: Path) -> Path:
    """Devuelve la carpeta con los archivos del paquete; descomprime con 7z si hace falta."""
    if p.is_dir():
        return p
    destino.mkdir(parents=True, exist_ok=True)
    if not any(destino.iterdir()):
        subprocess.run(["7z", "x", "-y", f"-o{destino}", str(p)], check=True, capture_output=True)
    return destino


def _archivo(carpeta: Path, nombre: str) -> Path:
    """Busca un archivo por nombre dentro de la carpeta (los paquetes a veces traen una subcarpeta)."""
    directo = carpeta / nombre
    if directo.exists():
        return directo
    for f in carpeta.rglob("*"):
        if f.name.lower() == nombre.lower():
            return f
    raise FileNotFoundError(f"{nombre} no está en {carpeta}")


# --------------------------------------------------------------------------- estado
class Estado:
    def __init__(self, trabajo: Path):
        self.ruta = trabajo / "estado.json"
        self.datos = json.loads(self.ruta.read_text()) if self.ruta.exists() else {"pasos": {}}

    def hecho(self, paso: str) -> bool:
        return "fin" in self.datos["pasos"].get(paso, {})

    def guardar(self, paso: str, **info) -> None:
        self.datos["pasos"][paso] = {**info, "fin": _ahora()}
        self.ruta.write_text(json.dumps(self.datos, indent=2, ensure_ascii=False, default=str))

    def info(self, paso: str) -> dict:
        return self.datos["pasos"].get(paso, {})

    def olvidar_desde(self, paso: str) -> None:
        for p in PASOS[PASOS.index(paso):]:
            self.datos["pasos"].pop(p, None)
        self.ruta.write_text(json.dumps(self.datos, indent=2, ensure_ascii=False, default=str))


# --------------------------------------------------------------------------- controles
def _stats_periodos(con, glob: str) -> dict[int, dict]:
    filas = con.execute(f"""
        SELECT periodo, count(*) filas, count(DISTINCT cuit) cuits, count(DISTINCT entidad) entidades,
               sum(monto)::DOUBLE monto
        FROM read_parquet('{_q(glob)}', hive_partitioning = true) WHERE tipo_id = 11 GROUP BY periodo
    """).fetchall()
    return {int(p): {"filas": f, "cuits": c, "entidades": e, "monto": m} for p, f, c, e, m in filas}


def _nivel(cambio: float, alerta: float, error: float) -> str:
    return "error" if abs(cambio) > error else "alerta" if abs(cambio) > alerta else "ok"


def controles(corte: int, nuevo_24dsf: Path, anterior_24dsf: Path, mensual: Path | None,
              control_24dsf: dict | None, referencias: Path, ipc_csv: Path, memoria: str = "10GB") -> dict:
    """Controles del corte nuevo. Devuelve ``{"filas": [...], "stats": {...}, "errores": n, "alertas": n}``.

    Cada fila: ``control``, ``nivel`` (ok/alerta/error) y ``detalle``. Las stats por período (filas, CUITs,
    entidades, monto) quedan para el registro ``_control/ingestas.csv``.
    """
    out: list[dict] = []

    def fila(control, nivel, detalle):
        out.append({"control": control, "nivel": nivel, "detalle": detalle})

    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{memoria}'; SET preserve_insertion_order = false;")
    nuevo = _stats_periodos(con, f"{nuevo_24dsf}/*/*.parquet")
    anterior = _stats_periodos(con, f"{anterior_24dsf}/*/*.parquet") if anterior_24dsf.exists() else {}

    # 1. archivo de control del BCRA
    if control_24dsf is not None:
        c = control_24dsf
        ok = c["pares_con_datos"] == c["pares_control"] and c["exactos"] + c["control_doble"] == c["pares_control"] \
            and c["pares_sin_control"] == 0
        fila("Totales contra 24DSF_control.txt", "ok" if ok else "error",
             f"{c['exactos'] + c['control_doble']:,} de {c['pares_control']:,} pares entidad × mes coinciden "
             f"({c['control_doble']:,} con el control duplicado, un rasgo conocido del archivo); "
             f"{c['pares_sin_control']:,} sin control; {c.get('pares_fuera_ventana', 0):,} del control fuera de los 24 meses")

    # 2. los 24 meses esperados
    esperados = {periodo_mas(corte, -k) for k in range(24)}
    faltan = sorted(esperados - set(nuevo))
    fila("24 meses terminando en el corte", "error" if faltan or max(nuevo, default=0) != corte else "ok",
         f"{min(nuevo, default='-')}–{max(nuevo, default='-')}" + (f"; faltan {faltan}" if faltan else ""))

    # 3. meses solapados contra el corte anterior (rectificaciones)
    solapados = sorted(set(nuevo) & set(anterior))
    peor, peor_p, peor_m = "ok", "ninguno", 0.0
    for p in solapados:
        for m in ("filas", "monto"):
            cambio = nuevo[p][m] / anterior[p][m] - 1 if anterior[p][m] else 0.0
            n = _nivel(cambio, TOL_SOLAPADO_ALERTA, TOL_SOLAPADO_ERROR)
            if abs(cambio) > abs(peor_m):
                peor_m, peor_p = cambio, f"{p} ({m})"
            if ["ok", "alerta", "error"].index(n) > ["ok", "alerta", "error"].index(peor):
                peor = n
    fila("Meses solapados contra el corte anterior", peor if solapados else "alerta",
         f"{len(solapados)} meses; mayor cambio {peor_m:+.2%} en {peor_p}" if solapados else "no hay corte anterior para comparar")

    # 4. mes nuevo contra el mes anterior del mismo corte (el 24DSF omite entidades en su primer mes:
    #    se compara contra el mensual si está)
    previo = periodo_mas(corte, -1)
    if corte in nuevo and previo in nuevo:
        base_corte = nuevo[corte]
        if mensual is not None and mensual.exists():
            r = con.execute(f"""SELECT count(*), count(DISTINCT cuit), count(DISTINCT entidad)
                                FROM read_parquet('{_q(mensual)}') WHERE tipo_id = 11 AND situacion > 0""").fetchone()
            base_corte = {"filas": r[0], "cuits": r[1], "entidades": r[2]}
            fila("Mensual completa al 24DSF en el mes de corte",
                 "ok" if r[2] >= nuevo[corte]["entidades"] else "alerta",
                 f"entidades: 24DSF {nuevo[corte]['entidades']}, mensual {r[2]}")
        cambio = base_corte["filas"] / nuevo[previo]["filas"] - 1
        fila("Filas del mes nuevo contra el mes anterior", _nivel(cambio, TOL_MES_NUEVO_ALERTA, TOL_MES_NUEVO_ERROR),
             f"{base_corte['filas']:,} contra {nuevo[previo]['filas']:,} ({cambio:+.2%})")

    # 5. entidades sin tipo en referencias/entidades.csv
    conocidas = {int(r["entidad"]) for r in csv.DictReader((referencias / "entidades.csv").open(encoding="utf-8"))}
    destinos = {int(r["entidad_origen"]) for r in csv.DictReader((referencias / "entidades_equivalencias.csv").open(encoding="utf-8"))}
    fuentes = [f"SELECT DISTINCT entidad FROM read_parquet('{_q(nuevo_24dsf)}/*/*.parquet') WHERE tipo_id = 11"]
    if mensual is not None and mensual.exists():
        fuentes.append(f"SELECT DISTINCT entidad FROM read_parquet('{_q(mensual)}') WHERE tipo_id = 11")
    presentes = {r[0] for r in con.execute(" UNION ".join(fuentes)).fetchall()}
    nuevas = sorted(presentes - conocidas - destinos)
    fila("Entidades con tipo asignado", "error" if nuevas else "ok",
         f"sin tipo en referencias/entidades.csv: {nuevas}" if nuevas else f"{len(presentes)} entidades, todas conocidas")

    # 6. IPC hasta el corte
    ipc = {int(r["periodo"]): int(r["imputado"]) for r in csv.DictReader(ipc_csv.open(encoding="utf-8"))}
    imputados = sorted(p for p in esperados if ipc.get(p) == 1)
    sin_ipc = sorted(p for p in esperados if p not in ipc)
    fila("IPC hasta el corte", "error" if sin_ipc else "alerta" if imputados else "ok",
         f"sin IPC: {sin_ipc}" if sin_ipc else f"imputados: {imputados} (rehacer cuando el INDEC publique)" if imputados
         else "publicado para los 24 meses")
    con.close()
    return {"filas": out, "stats": nuevo, "errores": sum(f["nivel"] == "error" for f in out),
            "alertas": sum(f["nivel"] == "alerta" for f in out)}


# --------------------------------------------------------------------------- monitoreo
def psi_fechas(dir_vars: Path, obs_ref: int, obs_nuevas: list[int], catalogo: Path, pct: int = 10,
               memoria: str = "10GB") -> list[dict]:
    """PSI de cada variable numérica (uso modelo) de ``obs_nuevas`` contra ``obs_ref``, con deciles de la
    referencia y los nulos como bin propio, sobre la población de modelado y una muestra por hash."""
    cat = cat_mod.cargar(catalogo)
    nums = [v["nombre"] for v in cat.variables if v["uso"] == "modelo" and v["tipo_dato"] != "CATEGORICA"]
    v2.verificar_muestra(dir_vars, pct)
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{memoria}'; SET preserve_insertion_order = false;")
    fechas = ", ".join(map(str, [obs_ref, *obs_nuevas]))
    fuente = f"read_parquet('{_q(dir_vars)}/periodo_obs=*/*.parquet', union_by_name = true)"
    presentes = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {fuente}").fetchall()}
    nums = [v for v in nums if v in presentes]   # una variable nueva del catálogo puede no estar en fechas viejas
    cols = ", ".join(f'"{v}"::DOUBLE AS "{v}"' for v in nums)
    con.execute(f"""CREATE TABLE mu AS SELECT PERIODO_OBS AS obs, {cols}
                    FROM {fuente}
                    WHERE PERIODO_OBS IN ({fechas}) AND EN_POBLACION = 1 AND hash(CUIT) % 100 < {pct}""")
    filas = []
    for v in nums:
        cortes = con.execute(f"""SELECT list_distinct(quantile_disc("{v}", [0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9]))
                                 FROM mu WHERE obs = {obs_ref}""").fetchone()[0]
        if not cortes:   # variable nula en la referencia (no disponible a esa fecha)
            continue
        dist = con.execute(f"""
            SELECT obs, CASE WHEN "{v}" IS NULL THEN -1 ELSE len(list_filter(?::DOUBLE[], lambda c: c < "{v}")) END AS bin,
                   count(*) n
            FROM mu GROUP BY ALL""", [cortes]).fetchall()
        tot: dict[int, int] = {}
        for obs, _, n in dist:
            tot[obs] = tot.get(obs, 0) + n
        p = {(obs, b): n / tot[obs] for obs, b, n in dist}
        bins = {b for _, b, _ in dist}
        for obs in obs_nuevas:
            if obs not in tot:
                continue
            psi = 0.0
            for b in bins:
                a, q = max(p.get((obs_ref, b), 0), 1e-4), max(p.get((obs, b), 0), 1e-4)
                psi += (q - a) * math.log(q / a)
            filas.append({"variable": v, "obs_ref": obs_ref, "obs": obs, "psi": round(psi, 6)})
    con.close()
    return filas


# --------------------------------------------------------------------------- registros en S3
def registrar(nombre: str, filas: list[dict], perfil: str = "fmargara", simular: bool = False) -> None:
    """Agrega filas a ``s3://data-cendeu-raw/_control/<nombre>`` (lo baja, suma y lo vuelve a subir)."""
    if not filas:
        return
    ruta = f"s3://{s3.BUCKET}/_control/{nombre}"
    try:
        actual = subprocess.run(["aws", "s3", "cp", ruta, "-", "--profile", perfil], capture_output=True, text=True,
                                check=True).stdout
    except subprocess.CalledProcessError:
        actual = ""
    previas = list(csv.DictReader(io.StringIO(actual))) if actual else []
    columnas = list(previas[0]) if previas else list(filas[0])
    for c in filas[0]:
        if c not in columnas:
            columnas.append(c)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columnas, extrasaction="ignore")
    w.writeheader()
    w.writerows(previas + filas)
    if simular:
        print(f"simulado: {len(filas)} filas nuevas en {ruta}", flush=True)
        return
    subprocess.run(["aws", "s3", "cp", "-", ruta, "--only-show-errors", "--profile", perfil],
                   input=buf.getvalue(), text=True, check=True)


# --------------------------------------------------------------------------- orquestación
def actualizar(corte: int, entrada: str, datos: str | Path, *, referencias: str | Path = "referencias",
               catalogo: str | Path = "catalogo/variables.yaml", reportes: str | Path = "reportes/actualizaciones",
               recalcular: bool = False, hasta: str | None = None, rehacer: str | None = None,
               forzar: bool = False, simular_s3: bool = False, perfil: str = "fmargara",
               memoria: str = "10GB") -> dict:
    """Corre los pasos pendientes del corte. ``hasta``: último paso a correr. ``rehacer``: vuelve a correr desde
    ese paso. ``forzar``: sigue aunque los controles den error. ``recalcular``: también rehace las variables de
    las fechas que ya existían (con la historia rectificada del corte nuevo)."""
    datos, ref, cat = Path(datos).expanduser(), Path(referencias), Path(catalogo)
    trabajo = datos / "_actualizaciones" / str(corte)
    trabajo.mkdir(parents=True, exist_ok=True)
    est = Estado(trabajo)
    if rehacer:
        est.olvidar_desde(rehacer)
    kw = {"memory_limit": memoria}
    ent = sincronizar_entrada(entrada, trabajo / "entrada", perfil)
    det = detectar(ent, corte)
    if not det["completo"]:
        raise SystemExit(f"Corte {corte} incompleto en {ent}: faltan {', '.join(det['faltan'])}")
    padron_corte = det["padron"]
    stg = trabajo / "nuevo"
    ultimo = PASOS[-1] if hasta is None else hasta

    def correr(paso: str) -> bool:
        if est.hecho(paso):
            print(f"[{paso}] ya hecho ({est.info(paso)['fin']})", flush=True)
            return False
        print(f"[{paso}] {_ahora()}", flush=True)
        return True

    for paso in PASOS[:PASOS.index(ultimo) + 1]:
        t0 = time.time()
        if paso == "convertir" and correr(paso):
            fuentes = trabajo / "fuentes"
            carpetas = {k: _extraer(Path(p), fuentes / Path(p).name.upper().removesuffix(".7Z").removesuffix(".ZIP"))
                        for k, p in det["encontrados"].items()}
            dsf = carpetas["24dsf"]
            shutil.rmtree(stg / "deudores_24m", ignore_errors=True)
            stg.mkdir(parents=True, exist_ok=True)
            pipeline.convertir(_archivo(dsf, "24DSF.txt"), stg / "deudores_24m", corte, **kw)
            ctrl = pipeline.validar_contra_control(stg / "deudores_24m", _archivo(dsf, "24DSF_control.txt"),
                                                   desde=periodo_mas(corte, -23), **kw)
            # el conversor del paquete espera <dir>/<NOMBRE>/<archivo>: se arma con enlaces a las carpetas extraídas
            vista = trabajo / "paquete"
            vista.mkdir(exist_ok=True)
            for k, nombre in {**paquetes(corte), "padron": f"{padron_corte}PADRON"}.items():
                if k != "24dsf" and not (vista / nombre).exists():
                    (vista / nombre).symlink_to(carpetas[k].resolve())
            filas = paquete.convertir_todo(vista, stg, corte, padron_corte, memoria, tmp=trabajo / "tmp_conv")
            shutil.rmtree(trabajo / "tmp_conv", ignore_errors=True)
            est.guardar(paso, control_24dsf=ctrl, filas_paquete=filas, padron=padron_corte, segundos=round(time.time() - t0))

        elif paso == "ipc" and correr(paso):
            ipc_nuevo = trabajo / "ipc.csv"
            previo = {r["periodo"]: r for r in csv.DictReader((ref / "ipc.csv").open())}
            primero = min([int(k) for k in previo] + [periodo_mas(corte, -23)])   # desde donde ya arrancaba
            try:
                pipeline.descargar_ipc(ipc_nuevo, primero, corte)
                nuevo = {r["periodo"]: r for r in csv.DictReader(ipc_nuevo.open())}
                previo.update(nuevo)     # el IPC nuevo pisa los meses que traiga (incluye los que antes eran imputados)
                with (ref / "ipc.csv").open("w", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=["periodo", "ipc", "imputado"], lineterminator="\n")
                    w.writeheader()
                    w.writerows(sorted(previo.values(), key=lambda r: int(r["periodo"])))
                nota = "actualizado desde la API del INDEC"
            except Exception as e:  # sin red: se sigue con el IPC versionado y el control lo marca
                nota = f"no se pudo bajar ({e}); se usa referencias/ipc.csv"
            est.guardar(paso, nota=nota, segundos=round(time.time() - t0))

        elif paso == "controles" and correr(paso):
            r = controles(corte, stg / "deudores_24m", datos / "deudores_24m",
                          stg / "deudores_mensual" / f"periodo={corte}" / "data.parquet",
                          est.info("convertir").get("control_24dsf"), ref, ref / "ipc.csv", memoria)
            for f in r["filas"]:
                print(f"  {f['nivel']:>6}  {f['control']}: {f['detalle']}", flush=True)
            est.guardar(paso, **r, segundos=round(time.time() - t0))
            if r["errores"] and not forzar:
                raise SystemExit(f"{r['errores']} controles con error: no se promueve nada. Revisar, corregir y correr "
                                 f"de nuevo con --rehacer controles (o --forzar).")

        elif paso == "promover" and correr(paso):
            viejos = trabajo / "reemplazados"
            movidos = []
            for origen in sorted((stg).rglob("*.parquet")):
                rel = origen.relative_to(stg)
                destino = datos / rel
                carpeta_dst = destino.parent
                if carpeta_dst.exists() and str(rel).startswith("deudores_24m"):
                    # una partición de período se reemplaza entera
                    (viejos / rel.parent).parent.mkdir(parents=True, exist_ok=True)
                    if not (viejos / rel.parent).exists():
                        shutil.move(str(carpeta_dst), str(viejos / rel.parent))
                carpeta_dst.mkdir(parents=True, exist_ok=True)
                shutil.move(str(origen), str(destino))
                movidos.append(str(rel))
            est.guardar(paso, archivos=len(movidos), reemplazados=str(viejos), segundos=round(time.time() - t0))

        elif paso == "base" and correr(paso):
            base_actual = datos / "v2" / "base"
            meta = json.loads((base_actual / "_meta.json").read_text()) if (base_actual / "_meta.json").exists() else {}
            base_nueva = trabajo / "base"
            shutil.rmtree(base_nueva, ignore_errors=True)
            v2.construir_base(datos / "deudores_24m", base_nueva, ref / "ipc.csv", ref,
                              mensual=datos / "deudores_mensual",
                              padron=datos / "padron" / f"corte={padron_corte}" / "padron.parquet",
                              uno_dsf=datos / "desde_sit1" / f"periodo={corte}" / "data.parquet",
                              base_ipc=meta.get("base_ipc"), reemplazo_mensual=[corte], **kw)
            if base_actual.exists():
                shutil.move(str(base_actual), str(trabajo / "base_anterior"))
            shutil.move(str(base_nueva), str(base_actual))
            est.guardar(paso, base_ipc=meta.get("base_ipc"), segundos=round(time.time() - t0))

        elif paso == "variables" and correr(paso):
            dir_vars = datos / "v2" / "variables"
            existentes = sorted(int(p.name.split("=")[1]) for p in dir_vars.glob("periodo_obs=*"))
            nuevas = sorted({periodo_mas(corte, -DESEMPENO), corte} - (set() if recalcular else set(existentes)))
            obs = sorted(set(nuevas) | (set(existentes) if recalcular else set()))
            vars_nuevas = trabajo / "variables"
            for o in obs:
                v2.construir_variables(datos / "v2" / "base", o, vars_nuevas, cat, retomar=True, **kw)
            anteriores = trabajo / "variables_anteriores"
            for o in obs:
                d = dir_vars / f"periodo_obs={o}"
                if d.exists():
                    anteriores.mkdir(exist_ok=True)
                    shutil.move(str(d), str(anteriores / d.name))
                shutil.move(str(vars_nuevas / f"periodo_obs={o}"), str(d))
            est.guardar(paso, obs=obs, nuevas=nuevas, referencia=max((e for e in existentes if e not in obs), default=None),
                        segundos=round(time.time() - t0))

        elif paso == "monitoreo" and correr(paso):
            info = est.info("variables")
            ref_obs = info.get("referencia")
            filas = psi_fechas(datos / "v2" / "variables", ref_obs, info["nuevas"], cat, memoria=memoria) if ref_obs else []
            with (trabajo / "psi.csv").open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["variable", "obs_ref", "obs", "psi"])
                w.writeheader()
                w.writerows(filas)
            altas = sorted((r for r in filas if r["psi"] >= PSI_ALERTA), key=lambda r: -r["psi"])
            est.guardar(paso, referencia=ref_obs, variables=len({r["variable"] for r in filas}),
                        psi_max=max((r["psi"] for r in filas), default=None), alertas=altas[:20],
                        segundos=round(time.time() - t0))

        elif paso == "subir" and correr(paso):
            subidos = _subir(corte, datos, padron_corte, est, ref, perfil, simular_s3)
            est.guardar(paso, simulado=simular_s3, **subidos, segundos=round(time.time() - t0))

        elif paso == "resumen" and correr(paso):
            ruta = escribir_resumen(corte, est, Path(reportes))
            est.guardar(paso, archivo=str(ruta))
    return est.datos


def _subir(corte: int, datos: Path, padron_corte: int, est: Estado, ref: Path, perfil: str, simular: bool) -> dict:
    hoy = _ahora() + "Z"
    stats = {int(k): v for k, v in est.info("controles").get("stats", {}).items()}
    filas_ing, filas_fue, filas_var, n = [], [], [], 0

    def subir(local: Path, prefijo: str, renombrar=None) -> list[dict]:
        nonlocal n
        r = s3.subir(local, prefijo, perfil, renombrar=renombrar, simular=simular)
        malos = [f for f in r if f["estado"] not in ("ok", "simulado")]
        if malos:
            raise RuntimeError(f"{len(malos)} archivos con tamaño distinto al subir {local}")
        n += len(r)
        return r

    for p in sorted(stats):
        r = subir(datos / "deudores_24m" / f"periodo={p}", f"deudores/periodo={p}", {"data_0.parquet": "data.parquet"})
        s = stats[p]
        filas_ing.append({"periodo": p, "corte_origen": f"24DSF{corte}", "fecha_carga": hoy, "filas": s["filas"],
                          "cuits": s["cuits"], "entidades": s["entidades"], "monto_total_miles": round(s["monto"], 1),
                          "bytes": sum(f["bytes"] for f in r), "ruta_s3": r[0]["ruta_s3"] if r else "", "estado": r[0]["estado"] if r else ""})
    filas_paq = est.info("convertir").get("filas_paquete", {})
    subidas: dict[Path, list[dict]] = {}
    for nombre, (src, _, dst) in paquete.trabajos(Path("."), datos, corte, padron_corte).items():
        rel = dst.relative_to(datos)
        if dst.parent not in subidas:   # padrón y rectificativas tienen dos archivos por carpeta
            subidas[dst.parent] = subir(dst.parent, str(rel.parent))
        r = [f for f in subidas[dst.parent] if f["archivo"] == dst.name]
        filas_fue.append({"fuente": nombre, "periodo": corte, "archivo_origen": "/".join(src.parts[-2:]),
                          "fecha_carga": hoy[:10], "filas": filas_paq.get(nombre, ""),
                          "bytes": sum(f["bytes"] for f in r), "ruta_s3": f"s3://{s3.BUCKET}/{rel}",
                          "estado": r[0]["estado"] if r else ""})
    for f in ("ipc.csv", "entidades.csv", "entidades_equivalencias.csv"):
        carpeta = "ipc" if f == "ipc.csv" else "entidades"
        tmp = datos / "_actualizaciones" / str(corte) / "referencias" / carpeta / f"corte={corte}"
        tmp.mkdir(parents=True, exist_ok=True)
        shutil.copy(ref / f, tmp / f)
    subir(datos / "_actualizaciones" / str(corte) / "referencias", "referencias", None)
    subir(datos / "v2" / "base", "base")
    n_cat = len(cat_mod.cargar("catalogo/variables.yaml").variables) if Path("catalogo/variables.yaml").exists() else ""
    for o in est.info("variables").get("obs", []):
        for f in subir(datos / "v2" / "variables" / f"periodo_obs={o}", f"variables/periodo_obs={o}"):
            if f["archivo"].endswith(".parquet"):
                filas_var.append({"periodo_obs": o, "parte": Path(f["archivo"]).stem, "base": "v2",
                                  "catalogo_variables": n_cat, "fecha_carga": hoy, "bytes": f["bytes"],
                                  "ruta_s3": f["ruta_s3"], "estado": f["estado"]})
    meta = json.loads((datos / "v2" / "base" / "_meta.json").read_text())
    fila_base = {"base": "v2", "fecha": meta["fecha"], "periodos": f"{meta['periodos'][0]}-{meta['periodos'][1]}",
                 "periodos_mensual": " ".join(map(str, meta["periodos_mensual"])), "base_ipc": meta["base_ipc"],
                 "ipc_base": meta["ipc_base"], "ipc_imputados": " ".join(map(str, meta["ipc_imputados"])) or "ninguno",
                 "hash_ipc": meta["archivos"]["ipc"], "hash_entidades": meta["archivos"]["entidades"],
                 "hash_equivalencias": meta["archivos"]["equivalencias"],
                 "ipc_s3": f"s3://{s3.BUCKET}/referencias/ipc/corte={corte}/ipc.csv", "ruta_local": str(datos / "v2" / "base"),
                 "base_s3": f"s3://{s3.BUCKET}/base/", "corte": corte}
    registrar("ingestas.csv", filas_ing, perfil, simular)
    registrar("fuentes.csv", filas_fue, perfil, simular)
    registrar("variables.csv", filas_var, perfil, simular)
    registrar("bases.csv", [fila_base], perfil, simular)
    return {"archivos": n, "periodos": len(filas_ing), "partes_variables": len(filas_var)}


def escribir_resumen(corte: int, est: Estado, dst_dir: Path) -> Path:
    """Resumen agregado del corte (sin datos personales) en ``reportes/actualizaciones/AAAAMM.md``."""
    i = est.info
    c, v, m, s = i("controles"), i("variables"), i("monitoreo"), i("subir")
    dur = sum(i(p).get("segundos", 0) for p in PASOS)
    out = [f"# Actualización del corte {corte}", "",
           f"Generado por `cendeu actualizar` el {_ahora()[:10]}. Duración total: {dur // 60} min.", "",
           "## Controles", "", "| Control | Resultado | Detalle |", "|---|---|---|"]
    out += [f"| {f['control']} | {f['nivel']} | {f['detalle']} |" for f in c.get("filas", [])]
    out += ["", f"IPC: {i('ipc').get('nota', '-')}.", "", "## Variables", "",
            f"- Fechas calculadas: {', '.join(map(str, v.get('obs', []))) or '-'} (nuevas: {', '.join(map(str, v.get('nuevas', []))) or '-'}).",
            f"- {corte} es la foto actual, sin target; {periodo_mas(corte, -DESEMPENO)} es la primera fecha con el desempeño a "
            f"{DESEMPENO} meses completo.", "", "## Estabilidad", ""]
    if m.get("referencia"):
        out += [f"PSI de {m['variables']} variables contra {m['referencia']}: máximo {m['psi_max']:.3f}."]
        if m.get("alertas"):
            out += ["", f"Variables con PSI ≥ {PSI_ALERTA}:", "", "| Variable | Fecha | PSI |", "|---|---|---|"]
            out += [f"| `{a['variable']}` | {a['obs']} | {a['psi']:.3f} |" for a in m["alertas"]]
        else:
            out += ["", f"Ninguna variable con PSI ≥ {PSI_ALERTA}."]
    else:
        out += ["Sin fecha de referencia para comparar."]
    out += ["", "## S3", "",
            ("Simulado: no se subió nada." if s.get("simulado") else
             f"{s.get('archivos', 0)} archivos subidos ({s.get('periodos', 0)} meses de deudores, "
             f"{s.get('partes_variables', 0)} partes de variables) y registrados en `_control/`.") if s else "Sin subir.", ""]
    dst_dir.mkdir(parents=True, exist_ok=True)
    ruta = dst_dir / f"{corte}.md"
    ruta.write_text("\n".join(out), encoding="utf-8")
    return ruta
