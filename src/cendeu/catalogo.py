"""Catálogo de variables v2: carga, expansión de familias y validación.

El catálogo (``catalogo/variables.yaml``) es la fuente única de nombres y definiciones. Este módulo
lo convierte en una lista plana de variables con todos sus campos resueltos.
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CAMPOS_REQUERIDOS = ("nombre", "bloque", "descripcion", "definicion", "fuente", "tipo_dato", "nulos", "uso", "sql")
TIPOS_DATO = {"DECIMAL", "INTEGER", "FLAG", "CATEGORICA"}
USOS = {"modelo", "futuro", "monitoreo", "exclusion"}
PATRON_NOMBRE = re.compile(r"^CEN_(?P<op>[A-Z]+)_(?P<desc>[A-Z0-9]+(?:_[A-Z0-9]+)*?)_(?P<ventana>\d+)M$")


@dataclass
class Catalogo:
    meta: dict
    variables: list[dict]
    errores: list[str] = field(default_factory=list)

    def por_bloque(self) -> dict[str, int]:
        cuenta: dict[str, int] = {}
        for v in self.variables:
            cuenta[v["bloque"]] = cuenta.get(v["bloque"], 0) + 1
        return cuenta


def _formatear(valor, contexto: dict):
    if isinstance(valor, str):
        try:
            return valor.format(**contexto)
        except (KeyError, IndexError, ValueError):
            # Llaves que no son de plantilla (p. ej. x_{t-1} en una fórmula): sustitución por clave.
            for k, v in contexto.items():
                valor = valor.replace("{" + k + "}", str(v))
            return valor
    return valor


def _expandir(entrada: dict, meta: dict) -> list[dict]:
    claves = entrada.get("expandir") or {}
    base = {k: v for k, v in entrada.items() if k != "expandir"}
    if not claves:
        return [base]
    salida = []
    for combo in itertools.product(*claves.values()):
        ctx = dict(zip(claves.keys(), combo))
        if isinstance(ctx.get("v"), int):  # límites de ventana ya resueltos: t-{v1}..t
            ctx["v1"], ctx["v2"] = ctx["v"] - 1, ctx["v"] - 2
        for k, val in list(ctx.items()):
            dicc = meta.get(k) if isinstance(meta.get(k), dict) else meta.get(k + "s")
            if isinstance(dicc, dict):  # serie → series, tipo → tipo: habilita {serie_desc}
                ctx[f"{k}_desc"] = dicc.get(val, val)
            if isinstance(meta.get(f"{k}_sql"), dict):  # op → op_sql: habilita {op_sql}
                ctx[f"{k}_sql"] = meta[f"{k}_sql"][val]
        salida.append({k: _formatear(v, ctx) for k, v in base.items()})
    return salida


def cargar(ruta: str | Path) -> Catalogo:
    meta = yaml.safe_load(Path(ruta).read_text(encoding="utf-8"))
    defaults = meta.get("defaults", {})
    variables: list[dict] = []
    for entrada in meta["variables"]:
        for v in _expandir(entrada, meta):
            variables.append({**defaults, **v})
    cat = Catalogo(meta=meta, variables=variables)
    cat.errores = validar(cat)
    for v in variables:
        m = PATRON_NOMBRE.match(v["nombre"])
        if m:
            v["operacion"] = m["op"]
            v["ventana_meses"] = int(m["ventana"])
    return cat


def validar(cat: Catalogo) -> list[str]:
    meta, errores, vistos = cat.meta, [], set()
    fuentes = set(meta["fuentes"])
    for v in cat.variables:
        n = v.get("nombre", "<sin nombre>")
        for c in CAMPOS_REQUERIDOS:
            if v.get(c) in (None, ""):
                errores.append(f"{n}: falta {c}")
        if n in vistos:
            errores.append(f"{n}: nombre duplicado")
        vistos.add(n)
        m = PATRON_NOMBRE.match(n)
        if not m:
            errores.append(f"{n}: no respeta CEN_<OPERACION>_<DESCRIPCION>_<n>M")
        elif m["op"] not in meta["operaciones"]:
            errores.append(f"{n}: operación {m['op']} no está en la tabla de operaciones")
        if v.get("bloque") not in meta["bloques"]:
            errores.append(f"{n}: bloque {v.get('bloque')} desconocido")
        for f in str(v.get("fuente", "")).split("+"):
            if f.strip() not in fuentes:
                errores.append(f"{n}: fuente {f.strip()} desconocida")
        if v.get("tipo_dato") not in TIPOS_DATO:
            errores.append(f"{n}: tipo_dato {v.get('tipo_dato')} inválido")
        if v.get("uso") not in USOS:
            errores.append(f"{n}: uso {v.get('uso')} inválido")
        for c in ("nombre", "descripcion", "sql"):
            if re.search(r"\{[a-z_]+\}", str(v.get(c, ""))):
                errores.append(f"{n}: plantilla sin resolver en {c}")
    return errores


COLUMNAS_DICCIONARIO = [
    "VARIABLE", "OPERACION", "DESCRIPCION", "VENTANA TIEMPO", "ORIGEN", "DEFINICION",
    "AGRUPADOR", "UNIDAD TIEMPO", "ACTUALIZACION", "TIPO DATO", "CLASE", "NULOS", "LINK", "OBSERVACIONES",
    # agregadas respecto de la plantilla CRC
    "BLOQUE", "USO", "SENTIDO ESPERADO", "UNIDAD", "DISPONIBLE DESDE", "SQL",
    # del informe de poder (`cendeu seleccion`)
    "IV", "GRUPO", "RECOMENDADA", "RECOMENDADA SIT1", "RECOMENDADA SIT2",
]
_TIPO = {"INTEGER": "int", "FLAG": "int", "DECIMAL": "float", "CATEGORICA": "string"}
_CLASE = {"INTEGER": "numeric", "DECIMAL": "numeric", "FLAG": "binary", "CATEGORICA": "category"}


def cargar_seleccion(ruta: str | Path | None) -> dict[str, dict]:
    """Lee ``catalogo/seleccion.csv`` (salida de ``cendeu seleccion``); vacío si no existe."""
    import csv
    if not ruta or not Path(ruta).exists():
        return {}
    with Path(ruta).open(encoding="utf-8") as f:
        return {r["variable"]: r for r in csv.DictReader(f)}


def _rec(s: dict | None, segmento: str | None = None) -> str:
    campo = f"recomendada_{segmento}" if segmento else "recomendada"
    if not s or campo not in s:
        return ""
    return "SI" if s[campo] == "True" else "NO"


def filas_diccionario(cat: Catalogo, seleccion: dict[str, dict] | None = None,
                      link_doc: str = "docs/variables.md") -> list[dict]:
    doc, fuentes, bloques = cat.meta.get("documentacion", {}), cat.meta["fuentes"], cat.meta["bloques"]
    seleccion = seleccion or {}
    filas = []
    for v in cat.variables:
        s = seleccion.get(v["nombre"])
        origen = " + ".join(f"{f.strip()} ({fuentes[f.strip()]})" for f in str(v["fuente"]).split("+"))
        filas.append({
            "VARIABLE": v["nombre"],
            "OPERACION": f"{v['operacion']} - {cat.meta['operaciones'][v['operacion']]}",
            "DESCRIPCION": v["descripcion"], "VENTANA TIEMPO": v["ventana_meses"], "ORIGEN": origen,
            "DEFINICION": " ".join(str(v["definicion"]).split()), "AGRUPADOR": doc.get("agrupador", "CUIT"),
            "UNIDAD TIEMPO": "MES", "ACTUALIZACION": str(v.get("actualizacion", "mensual")).upper(),
            "TIPO DATO": _TIPO[v["tipo_dato"]], "CLASE": _CLASE[v["tipo_dato"]],
            "NULOS": v["nulos"], "LINK": f"{link_doc}#{v['bloque'].lower()}",
            "OBSERVACIONES": " ".join(str(v.get("observaciones", "")).split()),
            "BLOQUE": v["bloque"], "USO": v["uso"], "SENTIDO ESPERADO": v.get("sentido", ""),
            "UNIDAD": v.get("unidad", ""), "DISPONIBLE DESDE": v.get("disponible_desde", ""), "SQL": v["sql"],
            "IV": s["iv"] if s else "", "GRUPO": s["grupo"] if s else "", "RECOMENDADA": _rec(s),
            "RECOMENDADA SIT1": _rec(s, "sit1"), "RECOMENDADA SIT2": _rec(s, "sit2"),
        })
    return filas


def escribir_diccionario(cat: Catalogo, dst: str | Path, seleccion: dict[str, dict] | None = None) -> Path:
    """CSV en UTF-8 con BOM (abre bien en Excel)."""
    import csv
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS_DICCIONARIO)
        w.writeheader()
        w.writerows(filas_diccionario(cat, seleccion))
    return dst


_INTRO = """# Variables CENDEU v2 — documento técnico

> Generado desde [`catalogo/variables.yaml`](../catalogo/variables.yaml) con `cendeu documentar`. No editar a mano:
> los cambios van al catálogo. Diccionario en formato plantilla: [`diccionario_variables.csv`](diccionario_variables.csv).

Variables de comportamiento crediticio por CUIT construidas sobre la Central de Deudores del BCRA, con el enfoque del
Feature Store CRC: diseñadas por dominio de riesgo, con dinámica temporal (VAR, TEN, FMX, RSI) y documentadas antes
de medir su poder. Contexto y decisiones: [PLAN.md](../PLAN.md), [estudio del 24DSF](../reportes/estudio_24dsf.md),
[fuentes BCRA y ARCA](../reportes/fuentes_bcra_arca.md).

## Nomenclatura

`CEN_<OPERACION>_<DESCRIPCION>_<VENTANA>`. `VENTANA` = `<n>M`, los últimos *n* meses terminando en la fecha de
observación (`1M` = el mes de observación). Ejemplo: `CEN_TEN_DEUDA_6M` = deuda actual sobre la de hace 6 meses.

## Cómo se construyen

1. **Base limpia** (`cendeu base`): una fila por CUIT × entidad × mes.
   - Período de corte desde el `deudores.txt` mensual y los 23 anteriores desde el 24DSF (el 24DSF omite entidades en su primer mes).
   - Sólo `tipo_id` 11; situación 11 → 1; duplicados CUIT × entidad × mes: peor situación y suma de montos.
   - Entidades absorbidas o recodificadas reemplazadas por su destino en toda la historia
     ([`referencias/entidades_equivalencias.csv`](../referencias/entidades_equivalencias.csv)).
   - CUITs dados de baja reemplazados por su CUIT de reemplazo (padrón ARCA).
   - Montos en miles de pesos constantes del mes base, deflactados con [`referencias/ipc.csv`](../referencias/ipc.csv)
     (IPC INDEC). Cada base guarda en `_meta.json` el hash del IPC usado y si había meses imputados.
2. **Series densas** (`cendeu variables-v2`): para la fecha de observación *t* se arman 13 meses (t-12..t) por CUIT,
   con ausencia = deuda 0, situación 0, 0 entidades. El mes t-12 sólo sirve como mes previo para altas y transiciones.
   Así las diferencias y el RSI nunca saltan meses (problema del Feature Store CRC).
3. **Variables**: cada una es una expresión SQL del catálogo sobre esas series. No hay SQL de variables escrito a mano.

## Población y target

"""


def escribir_documento(cat: Catalogo, dst: str | Path, seleccion: dict[str, dict] | None = None) -> Path:
    m, out, seleccion = cat.meta, [_INTRO], seleccion or {}
    for t in m["targets"]:
        out += [f"- **`{t['nombre']}`**: {t['descripcion']}. Definición: `{t['definicion']}`.",
                f"- **Población**: {' '.join(t['poblacion'].split())}",
                f"- {' '.join(t['observaciones'].split())}", ""]
    out += ["## Convenciones", ""] + [f"- **{k}**: {' '.join(str(v).split())}" for k, v in m["convenciones"].items()]
    out += ["", "## Fuentes", "", "| Código | Fuente |", "|---|---|"] + [f"| `{k}` | {v} |" for k, v in m["fuentes"].items()]
    out += ["", "## Series mensuales base", "", "| Serie | Contenido |", "|---|---|"] + \
           [f"| `{k}` | {v} |" for k, v in m["series"].items()]
    out += ["", "## Operaciones", "", "| Código | Significado |", "|---|---|"] + \
           [f"| `{k}` | {v} |" for k, v in m["operaciones"].items()]
    usos = {}
    for v in cat.variables:
        usos.setdefault(v["bloque"], {}).setdefault(v["uso"], 0)
        usos[v["bloque"]][v["uso"]] += 1
    out += ["", "## Resumen", "", f"{len(cat.variables)} variables.", "", "| Bloque | Qué mide | Variables | Uso |", "|---|---|---|---|"]
    for b, d in m["bloques"].items():
        u = ", ".join(f"{k}: {n}" for k, n in sorted(usos.get(b, {}).items()))
        out.append(f"| [{b}](#{b.lower()}) | {d} | {sum(usos.get(b, {}).values())} | {u} |")
    out += ["", "`uso`: **modelo** = candidata al score; **futuro** = sólo existe desde 202609 (mensual), entra cuando haya "
            "historia acumulada; **monitoreo** = no entra al score, sirve para medir sesgos; **exclusion** = define la población.",
            "", "Sentido esperado: relación con el riesgo (`+` = más valor, más riesgo). Se planteó como hipótesis y se "
            "corrigió con el informe de poder v2 donde salió al revés (la hipótesis original queda en las notas)."]
    if seleccion:
        recs = [v for v in cat.variables if _rec(seleccion.get(v["nombre"])) == "SI"]
        out += ["", "## Variables recomendadas", "",
                f"{len(recs)} variables marcadas como punto de partida para un modelo (columna **Rec.**): uso `modelo`, "
                "IV ≥ 0,1, PSI máximo < 0,1 y representante de su grupo de correlación (|Spearman| ≥ 0,8, la de mayor IV "
                "representa al grupo). Es una recomendación, no un recorte: todas las variables del catálogo se siguen "
                "calculando, y la elección final depende del modelo que las use (segmento, objetivo). Detalle y motivo de "
                "las no recomendadas en [`catalogo/seleccion.csv`](../catalogo/seleccion.csv); métricas en el "
                "[informe de poder](../reportes/poder_v2/)."]
        segs = [g for g in ("sit1", "sit2") if any(f"recomendada_{g}" in s for s in seleccion.values())]
        if segs:
            n = {g: sum(_rec(seleccion.get(v["nombre"]), g) == "SI" for v in cat.variables) for g in segs}
            out += ["", "Como la población mezcla situación 1 y situación 2, muchas variables de magnitud y calidad "
                    "separan sobre todo esos dos grupos. Por eso la selección se repite dentro de cada segmento, con el "
                    "mismo criterio y su propio informe de poder ([situación 1](../reportes/poder_v2_sit1/), "
                    "[situación 2](../reportes/poder_v2_sit2/)): "
                    + ", ".join(f"**{n[g]}** recomendadas en {g}" for g in segs)
                    + " (columnas **S1** y **S2**)."]
    for b, d in m["bloques"].items():
        out += ["", f'<a id="{b.lower()}"></a>', f"## {b} — {d}", "",
                "| Variable | Descripción | Definición | Nulos | Sentido | Uso | IV | Rec. | S1 | S2 |",
                "|---|---|---|---|---|---|---|---|---|---|"]
        for v in (x for x in cat.variables if x["bloque"] == b):
            defi = " ".join(str(v["definicion"]).split()).replace("|", "\\|")
            s = seleccion.get(v["nombre"])
            iv = f"{float(s['iv']):.2f}".replace(".", ",") if s else ""
            out.append(f"| `{v['nombre']}` | {v['descripcion']} | {defi} | {v['nulos']} | {v.get('sentido', '')} | {v['uso']} "
                       f"| {iv} | {_rec(s)} | {_rec(s, 'sit1')} | {_rec(s, 'sit2')} |")
        obs = [(v["nombre"], " ".join(str(v["observaciones"]).split())) for v in cat.variables
               if v["bloque"] == b and v.get("observaciones")]
        if obs:
            out += ["", "Notas:"] + [f"- `{n}`: {o}" for n, o in obs]
    out += ["", "## Cómo agregar o cambiar una variable", "",
            "1. Editar `catalogo/variables.yaml` (o agregar un valor a `expandir` de una familia existente).",
            "2. `cendeu catalogo` valida nombre, operación, bloque, fuente, tipo y uso.",
            "3. Agregar el caso a `tests/test_v2.py` si la lógica es nueva.",
            "4. `cendeu documentar` regenera este documento y el diccionario.", ""]
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(out), encoding="utf-8")
    return dst


if __name__ == "__main__":
    import sys

    c = cargar(sys.argv[1] if len(sys.argv) > 1 else "catalogo/variables.yaml")
    print(f"{len(c.variables)} variables")
    for b, k in c.por_bloque().items():
        print(f"  {b}: {k}")
    usos: dict[str, int] = {}
    for v in c.variables:
        usos[v["uso"]] = usos.get(v["uso"], 0) + 1
    print("uso:", usos)
    print("\n".join(c.errores) or "sin errores")
    sys.exit(1 if c.errores else 0)
