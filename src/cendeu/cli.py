"""CLI: ``cendeu <comando>``. Ejecutar ``cendeu -h`` para ver la ayuda."""
from __future__ import annotations

import argparse
import json

from . import pipeline, s3, scorecard, scoring, v2


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="cendeu", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ap.add_argument("--memoria", default="10GB", help="memory_limit de DuckDB (default 10GB)")

    p = sub.add_parser("ipc", help="descarga IPC INDEC a CSV")
    p.add_argument("--desde", type=int, required=True); p.add_argument("--hasta", type=int, required=True)
    p.add_argument("--salida", required=True)

    p = sub.add_parser("convertir", help="24DSF.txt -> Parquet largo particionado por período")
    p.add_argument("txt"); p.add_argument("salida"); p.add_argument("--corte", type=int, required=True)
    p.add_argument("--control", help="24DSF_control.txt para validar al terminar")

    p = sub.add_parser("variables", help="variables por CUIT a una fecha de observación")
    p.add_argument("parquet_dir"); p.add_argument("salida")
    p.add_argument("--obs", type=int, required=True); p.add_argument("--ipc", required=True)
    p.add_argument("--historia", type=int, default=12); p.add_argument("--desempeno", type=int, default=0)

    p = sub.add_parser("iv", help="IV y Gini univariados")
    p.add_argument("variables"); p.add_argument("--resumen", required=True); p.add_argument("--bins", required=True)

    p = sub.add_parser("entrenar", help="entrena scorecard WoE + logística")
    p.add_argument("variables"); p.add_argument("modelo_json")
    p.add_argument("--pct-muestra", type=int, default=20); p.add_argument("--max-vars", type=int, default=12)

    p = sub.add_parser("scorear", help="aplica el scorecard a un Parquet de variables")
    p.add_argument("variables"); p.add_argument("modelo_json"); p.add_argument("salida")

    p = sub.add_parser("base", help="v2: base limpia (24DSF + mensual, equivalencias, IPC) por período y bucket")
    p.add_argument("dir_24dsf"); p.add_argument("salida")
    p.add_argument("--ipc", default="referencias/ipc.csv"); p.add_argument("--referencias", default="referencias")
    p.add_argument("--mensual"); p.add_argument("--padron"); p.add_argument("--uno-dsf")
    p.add_argument("--base-ipc", type=int, help="período de los pesos constantes (default: último del IPC)")
    p.add_argument("--desde", type=int, help="retomar desde este período (los anteriores ya están escritos)")

    p = sub.add_parser("variables-v2", help="v2: variables del catálogo a una o más fechas de observación")
    p.add_argument("dir_base"); p.add_argument("salida")
    p.add_argument("--obs", type=int, nargs="+", required=True)
    p.add_argument("--catalogo", default="catalogo/variables.yaml")
    p.add_argument("--buckets", type=int, nargs="*", help="sólo estos buckets (prueba)")

    p = sub.add_parser("catalogo", help="v2: valida el catálogo y muestra el conteo por bloque")
    p.add_argument("--catalogo", default="catalogo/variables.yaml")

    p = sub.add_parser("documentar", help="v2: genera docs/variables.md y docs/diccionario_variables.csv desde el catálogo")
    p.add_argument("--catalogo", default="catalogo/variables.yaml"); p.add_argument("--docs", default="docs")

    p = sub.add_parser("poder", help="v2: informe de poder (IV, Gini, KS, PSI, correlaciones, incremental vs v1)")
    p.add_argument("dir_variables"); p.add_argument("--salida", default="reportes/poder_v2")
    p.add_argument("--catalogo", default="catalogo/variables.yaml")
    p.add_argument("--oot", type=int, nargs="*", help="fechas out-of-time (default: las dos últimas)")
    p.add_argument("--pct-muestra", type=int, default=10)
    p.add_argument("--vars-v1", help="Parquet de variables v1 para el valor incremental")
    p.add_argument("--modelo-v1", default="modelos/scorecard_cendeu_202509.json"); p.add_argument("--obs-v1", type=int, default=202509)

    p = sub.add_parser("subir", help="sube un directorio a s3://data-cendeu-raw/<prefijo> verificando tamaños")
    p.add_argument("dir_local"); p.add_argument("prefijo", help="p. ej. variables o deudores")
    p.add_argument("--perfil", default="fmargara")
    p.add_argument("--renombrar", nargs="*", default=[], help="local=s3, p. ej. data_0.parquet=data.parquet")
    p.add_argument("--simular", action="store_true", help="sólo muestra qué subiría")

    a = ap.parse_args(argv)
    kw = {"memory_limit": a.memoria}
    if a.cmd == "ipc":
        print(pipeline.descargar_ipc(a.salida, a.desde, a.hasta))
    elif a.cmd == "convertir":
        pipeline.convertir(a.txt, a.salida, a.corte, **kw)
        if a.control:
            print(json.dumps(pipeline.validar_contra_control(a.salida, a.control, **kw), indent=2))
    elif a.cmd == "variables":
        pipeline.construir_variables(a.parquet_dir, a.obs, a.salida, a.ipc, a.historia, a.desempeno, **kw)
    elif a.cmd == "iv":
        print(pipeline.iv_gini(a.variables, a.resumen, a.bins, **kw).to_string(index=False))
    elif a.cmd == "entrenar":
        scorecard.entrenar(a.variables, a.modelo_json, pct_muestra=a.pct_muestra, max_vars=a.max_vars)
    elif a.cmd == "base":
        v2.construir_base(a.dir_24dsf, a.salida, a.ipc, a.referencias, mensual=a.mensual, padron=a.padron,
                          uno_dsf=a.uno_dsf, base_ipc=a.base_ipc, desde=a.desde, **kw)
    elif a.cmd == "variables-v2":
        for obs in a.obs:
            v2.construir_variables(a.dir_base, obs, a.salida, a.catalogo, buckets=a.buckets, **kw)
    elif a.cmd == "documentar":
        from pathlib import Path
        from . import catalogo
        c = catalogo.cargar(a.catalogo)
        if c.errores:
            raise SystemExit("\n".join(c.errores))
        print(catalogo.escribir_diccionario(c, Path(a.docs) / "diccionario_variables.csv"))
        print(catalogo.escribir_documento(c, Path(a.docs) / "variables.md"))
    elif a.cmd == "poder":
        from . import poder
        r = poder.informe(a.dir_variables, a.catalogo, a.salida, fechas_oot=a.oot, pct_muestra=a.pct_muestra,
                          vars_v1=a.vars_v1, modelo_v1=a.modelo_v1 if a.vars_v1 else None, obs_v1=a.obs_v1, memory_limit=a.memoria)
        print(r.head(25).to_string(index=False))
    elif a.cmd == "subir":
        filas = s3.subir(a.dir_local, a.prefijo, a.perfil, renombrar=dict(r.split("=", 1) for r in a.renombrar), simular=a.simular)
        malos = [f for f in filas if f["estado"] not in ("ok", "simulado")]
        if malos:
            raise SystemExit(f"{len(malos)} archivos con tamaño distinto")
    elif a.cmd == "catalogo":
        from . import catalogo
        c = catalogo.cargar(a.catalogo)
        print(f"{len(c.variables)} variables", c.por_bloque())
        if c.errores:
            raise SystemExit("\n".join(c.errores))
    elif a.cmd == "scorear":
        scoring.scorear_parquet(a.variables, a.modelo_json, a.salida)


if __name__ == "__main__":
    main()
