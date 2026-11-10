"""CLI: ``cendeu <comando>``. Ejecutar ``cendeu -h`` para ver la ayuda."""
from __future__ import annotations

import argparse
import json

from . import pipeline, scorecard, scoring


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
    elif a.cmd == "scorear":
        scoring.scorear_parquet(a.variables, a.modelo_json, a.salida)


if __name__ == "__main__":
    main()
