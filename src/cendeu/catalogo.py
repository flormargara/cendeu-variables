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

CAMPOS_REQUERIDOS = ("nombre", "bloque", "descripcion", "definicion", "fuente", "tipo_dato", "nulos", "uso")
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
        for c in ("nombre", "descripcion"):
            if re.search(r"\{[a-z_]+\}", str(v.get(c, ""))):
                errores.append(f"{n}: plantilla sin resolver en {c}")
    return errores


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
