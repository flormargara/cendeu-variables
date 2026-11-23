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
    p.add_argument("--retomar", action="store_true", help="saltea las partes ya escritas")
    p.add_argument("--pct-muestra", type=int, help="sólo este %% de CUITs (hash), para desarrollo; no se puede subir a S3")

    p = sub.add_parser("catalogo", help="v2: valida el catálogo y muestra el conteo por bloque")
    p.add_argument("--catalogo", default="catalogo/variables.yaml")

    p = sub.add_parser("documentar", help="v2: genera docs/variables.md y docs/diccionario_variables.csv desde el catálogo")
    p.add_argument("--catalogo", default="catalogo/variables.yaml"); p.add_argument("--docs", default="docs")
    p.add_argument("--seleccion", default="catalogo/seleccion.csv", help="salida de `cendeu seleccion` (si existe)")

    p = sub.add_parser("poder", help="v2: informe de poder (IV, Gini, KS, PSI, correlaciones, incremental vs v1)")
    p.add_argument("dir_variables"); p.add_argument("--salida", default="reportes/poder_v2")
    p.add_argument("--catalogo", default="catalogo/variables.yaml")
    p.add_argument("--oot", type=int, nargs="*", help="fechas out-of-time (default: las dos últimas)")
    p.add_argument("--pct-muestra", type=int, default=10)
    p.add_argument("--vars-v1", help="Parquet de variables v1 para el valor incremental")
    p.add_argument("--modelo-v1", default="modelos/scorecard_cendeu_202509.json"); p.add_argument("--obs-v1", type=int, default=202509)
    p.add_argument("--segmento", choices=["sit1", "sit2"], help="sólo situación 1 o 2 en el mes de observación")

    p = sub.add_parser("seleccion", help="v2: marca las variables recomendadas (IV, PSI, grupo) en catalogo/seleccion.csv")
    p.add_argument("--reporte", default="reportes/poder_v2"); p.add_argument("--salida", default="catalogo/seleccion.csv")
    p.add_argument("--segmentos", nargs="*", default=["sit1=reportes/poder_v2_sit1", "sit2=reportes/poder_v2_sit2"],
                   help="NOMBRE=informe de poder del segmento (se omiten los que no existen)")

    p = sub.add_parser("pagina-poder", help="v2: arma los CSV de la página del informe de poder")
    p.add_argument("--reporte", default="reportes/poder_v2"); p.add_argument("--catalogo", default="catalogo/variables.yaml")
    p.add_argument("--salida", default="reportes/poder_v2/pagina")

    p = sub.add_parser("validar-v2", help="v2: validación multivariada de las variables (ajuste por segmento, fuera de muestra, vs v1)")
    p.add_argument("dir_variables"); p.add_argument("--salida", default="reportes/validacion_v2")
    p.add_argument("--catalogo", default="catalogo/variables.yaml")
    p.add_argument("--vars-v1", nargs="*", default=[], help="OBS=Parquet de variables v1, p. ej. 202603=data/vars_202603_d6.parquet")
    p.add_argument("--modelo-v1", default="modelos/scorecard_cendeu_202509.json")

    p = sub.add_parser("actualizar", help="corte mensual: de los paquetes de ARCA a base, variables y S3, con controles")
    p.add_argument("--entrada", default="~/Downloads", help="carpeta (o s3://…) con los paquetes bajados de ARCA")
    p.add_argument("--datos", required=True, help="carpeta de datos (deudores_24m, deudores_mensual, …, v2/base, v2/variables)")
    p.add_argument("--corte", type=int, help="AAAAMM (default: el mes siguiente al último de deudores_24m)")
    p.add_argument("--detectar", action="store_true", help="sólo dice si el corte está completo en la entrada (sale con 1 si falta algo)")
    p.add_argument("--hasta", choices=["convertir", "ipc", "controles", "promover", "base", "variables", "monitoreo", "subir", "resumen"],
                   help="último paso a correr (p. ej. controles para revisar antes de promover)")
    p.add_argument("--rehacer", choices=["convertir", "ipc", "controles", "promover", "base", "variables", "monitoreo", "subir", "resumen"],
                   help="vuelve a correr desde este paso")
    p.add_argument("--recalcular", action="store_true", help="rehace también las variables de las fechas que ya existían")
    p.add_argument("--forzar", action="store_true", help="sigue aunque algún control dé error")
    p.add_argument("--simular-s3", action="store_true", help="no sube nada: sólo muestra qué subiría")
    p.add_argument("--perfil", default="fmargara")

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
            v2.construir_variables(a.dir_base, obs, a.salida, a.catalogo, buckets=a.buckets, retomar=a.retomar,
                                   pct_muestra=a.pct_muestra, **kw)
    elif a.cmd == "documentar":
        from pathlib import Path
        from . import catalogo
        c = catalogo.cargar(a.catalogo)
        if c.errores:
            raise SystemExit("\n".join(c.errores))
        sel = catalogo.cargar_seleccion(a.seleccion)
        print(catalogo.escribir_diccionario(c, Path(a.docs) / "diccionario_variables.csv", sel))
        print(catalogo.escribir_documento(c, Path(a.docs) / "variables.md", sel))
    elif a.cmd == "poder":
        from . import poder
        r = poder.informe(a.dir_variables, a.catalogo, a.salida, fechas_oot=a.oot, pct_muestra=a.pct_muestra,
                          vars_v1=a.vars_v1, modelo_v1=a.modelo_v1 if a.vars_v1 else None, obs_v1=a.obs_v1, memory_limit=a.memoria,
                          segmento=a.segmento)
        print(r.head(25).to_string(index=False))
    elif a.cmd == "seleccion":
        from . import poder
        from pathlib import Path
        segs = {n: d for n, d in (x.split("=", 1) for x in a.segmentos) if (Path(d) / "resumen.csv").exists()}
        s = poder.seleccion(a.reporte, a.salida, segs)
        print(f"{int(s.recomendada.sum())} recomendadas de {len(s)} evaluadas -> {a.salida}")
        for n in segs:
            print(f"  {n}: {int(s[f'recomendada_{n}'].sum())} recomendadas")
    elif a.cmd == "pagina-poder":
        from . import poder
        print(f"{poder.actualizar_sentidos(a.reporte, a.catalogo)} variables con sentido esperado actualizado")
        for r in poder.archivos_pagina(a.reporte, a.catalogo, a.salida):
            print(r)
    elif a.cmd == "validar-v2":
        from . import validacion_v2
        v1 = {int(o): r for o, r in (x.split("=", 1) for x in a.vars_v1)}
        validacion_v2.validar(a.dir_variables, a.catalogo, a.salida, vars_v1=v1 or None,
                              modelo_v1=a.modelo_v1 if v1 else None)
    elif a.cmd == "actualizar":
        from pathlib import Path
        from . import actualizar
        corte = a.corte or actualizar.proximo_corte(Path(a.datos).expanduser())
        if a.detectar:
            entrada = actualizar.sincronizar_entrada(a.entrada, Path(a.datos).expanduser() / "_actualizaciones" / str(corte) / "entrada", a.perfil)
            d = actualizar.detectar(entrada, corte)
            print(json.dumps(d, indent=2, ensure_ascii=False))
            raise SystemExit(0 if d["completo"] else 1)
        actualizar.actualizar(corte, a.entrada, a.datos, recalcular=a.recalcular, hasta=a.hasta, rehacer=a.rehacer,
                              forzar=a.forzar, simular_s3=a.simular_s3, perfil=a.perfil, memoria=a.memoria)
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
