"""Convierte los paquetes mensuales del BCRA/ARCA (ancho fijo, latin-1) a Parquet.

Diseños de registro tomados de "LEAME DEUDORES.pdf" (v. 2026-01), "LEAME PADRON.pdf" (v1.0) y
"LEAME CHEQUES.pdf" (2024-12).
Cada archivo se valida por largo de registro antes de convertir: si alguna línea no mide lo
que dice el LEAME, se aborta (el BCRA cambió el diseño).

Uso:
    python -m cendeu.paquete <dir_downloads> <dir_salida> --periodo 202609 --padron 20261030 [--cheques 20261031]
    (``cendeu actualizar`` lo llama con ``trabajos``/``convertir_todo``).
"""
import argparse
import pathlib
import time

import duckdb

# (nombre, longitud, tipo) en orden. Tipos: int, str, monto (miles de $ "1006,0" con coma), fecha_aaaamm
DEUDORES = [
    ("entidad", 5, "int"), ("periodo", 6, "int"), ("tipo_id", 2, "int"), ("cuit", 11, "int"),
    ("actividad", 3, "int"), ("situacion", 2, "int"),
    ("prestamos", 12, "monto"), ("sin_uso", 12, "skip"), ("garantias_otorgadas", 12, "monto"),
    ("otros_conceptos", 12, "monto"), ("gtia_pref_a", 12, "monto"), ("gtia_pref_b", 12, "monto"),
    ("sin_gtia_pref", 12, "monto"), ("contragtia_pref_a", 12, "monto"), ("contragtia_pref_b", 12, "monto"),
    ("sin_contragtia_pref", 12, "monto"), ("previsiones", 12, "monto"),
    ("deuda_cubierta", 1, "int"), ("proceso_judicial", 1, "int"), ("refinanciacion", 1, "int"),
    ("recategorizacion_oblig", 1, "int"), ("situacion_juridica", 1, "int"), ("irrecuperable_disp_tecnica", 1, "int"),
    ("dias_atraso", 4, "int"),
]
RECTIFICATIVAS = [
    ("entidad", 5, "int"), ("periodo", 6, "int"), ("tipo_id", 2, "int"), ("cuit", 11, "int"),
    ("denominacion", 55, "skip"), ("situacion", 2, "int"), ("monto", 12, "monto"),
]
UNO_DSF = [("tipo_id", 2, "int"), ("cuit", 11, "int"), ("desde_sit1", 6, "int")]
MAEENT = [("entidad", 5, "int"), ("nombre", 70, "str")]
PADRON = [
    ("cuit", 11, "int"), ("denominacion", 160, "skip"), ("actividad", 6, "int"), ("baja", 1, "str"),
    ("cuit_reemplazo", 11, "int"), ("fecha_nac_contrato", 10, "str"), ("sexo", 1, "str"),
    ("cod_postal", 10, "str"), ("provincia", 2, "int"), ("fecha_fallecimiento", 8, "str"),
]
# El LEAME dice 254 para la descripción, pero el archivo real (corte 20261030) trae 260.
ACTIVIDADES = [("actividad", 6, "int"), ("descripcion", 260, "str")]
# Nomdeu.txt (CUIT + nombre) no se convierte: sólo nombres de personas, sin uso analítico.
# Central de Cheques Rechazados ("LEAME CHEQUES.pdf"): foto completa al día del archivo (AAAAMMDDCHEQUES).
# Con cuit_relacionado informado, la fila es el cheque de esa persona jurídica repetido en cada persona vinculada.
CHEQUES = [
    ("cuit", 11, "int"), ("nro_cheque", 10, "int"), ("fecha_rechazo", 8, "fecha"), ("monto", 15, "centavos"),
    ("causal", 1, "int"), ("fecha_levantamiento", 8, "fecha"), ("revision", 1, "str"), ("judicial", 1, "str"),
    ("cuit_relacionado", 11, "int"), ("pago_multa", 15, "str"),
]


def _expr(campos):
    cols, pos = [], 1
    for nombre, largo, tipo in campos:
        s = f"substr(l, {pos}, {largo})"
        if tipo == "int":
            cols.append(f"TRY_CAST(nullif(trim({s}), '') AS BIGINT) AS {nombre}")
        elif tipo == "monto":
            cols.append(f"TRY_CAST(replace(nullif(trim({s}), ''), ',', '.') AS DECIMAL(14,1)) AS {nombre}")
        elif tipo == "centavos":   # pesos con las dos últimas posiciones como decimales
            cols.append(f"(TRY_CAST(nullif(trim({s}), '') AS BIGINT) / 100)::DECIMAL(15,2) AS {nombre}")
        elif tipo == "fecha":      # AAAAMMDD
            cols.append(f"TRY_STRPTIME(nullif(trim({s}), ''), '%Y%m%d')::DATE AS {nombre}")
        elif tipo == "str":
            cols.append(f"nullif(trim({s}), '') AS {nombre}")
        pos += largo
    return ", ".join(cols), pos - 1


def _recodificar(src: pathlib.Path, tmp: pathlib.Path) -> pathlib.Path:
    """Copia UTF-8 reemplazando bytes de control (0x00-0x1F, 0x80-0x9F) por '?', 1 byte -> 1 carácter.
    Algunos archivos traen basura en los nombres que DuckDB no acepta como latin-1."""
    tabla = {i: "?" for i in [*range(0x20), *range(0x80, 0xA0)] if i not in (10, 13)}
    dst = tmp / (src.name + ".utf8")
    with open(src, encoding="latin-1", newline="") as f, open(dst, "w", encoding="utf-8", newline="") as g:
        for linea in f:
            if linea.strip("\x1a\r\n"):
                g.write(linea.translate(tabla))
    return dst


def convertir(c, src: pathlib.Path, campos, dst: pathlib.Path, tmp: pathlib.Path):
    sel, largo = _expr(campos)

    def vista(path, enc):
        c.execute(f"""CREATE OR REPLACE VIEW raw AS
            SELECT rtrim(l, chr(13)) AS l FROM read_csv('{path}', columns = {{'l': 'VARCHAR'}}, delim = chr(1),
                 quote = '', escape = '', header = false, auto_detect = false, encoding = '{enc}')""")
        return c.execute(f"SELECT count(*) FILTER (length(l) <> {largo}), count(*) FROM raw").fetchone()

    try:
        malos = vista(src, "latin-1")
    except duckdb.InvalidInputException:
        print(f"{src.name}: bytes fuera de latin-1, recodificando", flush=True)
        malos = vista(_recodificar(src, tmp), "utf-8")
    if malos[0]:
        raise ValueError(f"{src.name}: {malos[0]} de {malos[1]} líneas no miden {largo}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    c.execute(f"COPY (SELECT {sel} FROM raw) TO '{dst}' (FORMAT parquet, COMPRESSION zstd)")
    return malos[1]


def trabajos(d: pathlib.Path, o: pathlib.Path, p: int, padron: int, cheques: int | None = None) -> dict:
    """{nombre: (archivo de origen, diseño, destino)} del paquete mensual ``p``, el padrón ``padron`` y, si se
    indica, la foto de cheques rechazados ``cheques`` (AAAAMMDD)."""
    extra = {"cheques": (d / f"{cheques}CHEQUES/cheques.txt", CHEQUES, o / f"cheques/corte={cheques}/data.parquet")} \
        if cheques else {}
    return extra | {
        "maeent": (d / f"{p}DEUDORES/Maeent.txt", MAEENT, o / f"entidades/periodo={p}/maeent.parquet"),
        "1dsf": (d / f"1DSF{p}/1DSF.txt", UNO_DSF, o / f"desde_sit1/periodo={p}/data.parquet"),
        "inf_ret": (d / f"{p}INFRET/inf_ret.txt", RECTIFICATIVAS, o / f"rectificativas/publicado={p}/inf_ret.parquet"),
        "inf_retparc": (d / f"{p}INFRETPA/inf_retparc.txt", RECTIFICATIVAS, o / f"rectificativas/publicado={p}/inf_retparc.parquet"),
        "actividades": (d / f"{padron}PADRON/Actividades_ARCA.txt", ACTIVIDADES, o / f"padron/corte={padron}/actividades.parquet"),
        "deudores": (d / f"{p}DEUDORES/deudores.txt", DEUDORES, o / f"deudores_mensual/periodo={p}/data.parquet"),
        "padron": (d / f"{padron}PADRON/Padron_ARCA.txt", PADRON, o / f"padron/corte={padron}/padron.parquet"),
    }


def convertir_todo(d: pathlib.Path, o: pathlib.Path, p: int, padron: int, memoria: str = "8GB",
                   tmp: pathlib.Path | None = None, solo: list[str] | None = None,
                   cheques: int | None = None) -> dict[str, int]:
    """Convierte el paquete mensual, el padrón y (si se indica) la foto de cheques; devuelve las filas de cada archivo."""
    tmp = tmp or o / "_tmp_conv"
    tmp.mkdir(parents=True, exist_ok=True)
    c = duckdb.connect()
    c.execute(f"SET memory_limit = '{memoria}'; SET preserve_insertion_order = false;")
    filas = {}
    for nombre, (src, campos, dst) in trabajos(d, o, p, padron, cheques).items():
        if solo and nombre not in solo:
            continue
        t = time.time()
        filas[nombre] = convertir(c, src, campos, dst, tmp)
        print(f"{nombre}: {filas[nombre]:,} filas, {time.time() - t:.0f}s -> {dst}", flush=True)
    c.close()
    return filas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("downloads"); ap.add_argument("salida")
    ap.add_argument("--periodo", type=int, required=True); ap.add_argument("--padron", type=int, required=True)
    ap.add_argument("--cheques", type=int, help="fecha AAAAMMDD de la foto de cheques rechazados (AAAAMMDDCHEQUES)")
    ap.add_argument("--memoria", default="8GB"); ap.add_argument("--solo", nargs="*")
    ap.add_argument("--tmp", default="/tmp/cendeu_conv", help="dir para copias recodificadas")
    a = ap.parse_args()
    convertir_todo(pathlib.Path(a.downloads), pathlib.Path(a.salida), a.periodo, a.padron, a.memoria,
                   pathlib.Path(a.tmp), a.solo, a.cheques)


if __name__ == "__main__":
    main()
