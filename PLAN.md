# Plan v2 — Variables CENDEU

Rediseño de las variables de comportamiento sobre la Central de Deudores (BCRA, archivo `24DSF`), siguiendo el enfoque del Feature Store CRC: variables diseñadas por dominio de riesgo (no por disponibilidad), dinámica temporal (VAR, TEN, FMX, RSI), documentación completa y medición de poder antes de usarlas.

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Nomenclatura | Unificada: `CEN_<OPERACION>_<DESCRIPCION>_<VENTANA>`. No se mantienen los nombres v1. |
| Ventanas | 3, 6 y 12 meses. |
| Target | Situación ≥3 en los **6 meses** siguientes (permite varias fechas de observación, validación *out-of-time* y PSI). Target a 12 meses queda para iteraciones futuras. |
| IPC | INDEC vía datos.gob.ar, versionado en [`referencias/ipc.csv`](referencias/ipc.csv) y copiado por corte a S3; cada base registra su hash y meses imputados en `_meta.json`. |
| Almacenamiento | `s3://data-cendeu-raw` (perfil `fmargara`, política [`infra/cendeu-s3-policy.json`](infra/cendeu-s3-policy.json)), una carpeta por período. |
| v1 | Variables y scores v1 se suben sólo como línea de base; se borran cuando exista v2 (*fresh start*). |
| Repo | Se mantiene el repo actual; el rediseño se hace sobre él. |

## Layout S3

```
s3://data-cendeu-raw/
  deudores/periodo=AAAAMM/data.parquet        un mes de la central, todas las entidades
  variables/periodo_obs=AAAAMM/…              variables por CUIT a esa fecha
  scores/periodo_obs=AAAAMM/…
  deudores_mensual/periodo=AAAAMM/data.parquet  central mensual completa (24 campos)
  entidades/periodo=AAAAMM/maeent.parquet     maestro de entidades
  desde_sit1/periodo=AAAAMM/data.parquet      1DSF: desde cuándo en situación 1
  rectificativas/publicado=AAAAMM/…           inf_ret e inf_retparc
  padron/corte=AAAAMMDD/…                     padrón ARCA y nomenclador de actividades
  docs/                                       diccionario e informes
  _control/ingestas.csv                       registro por período del bianual: corte de origen, filas, CUITs, entidades, monto, estado
  referencias/ipc|entidades/corte=AAAAMM/      IPC y tablas de entidades usadas en cada corte
  _control/bases.csv                          una fila por base v2 construida (IPC, imputados, hashes)
  _control/fuentes.csv                        registro de las demás fuentes: archivo de origen, filas, bytes, ruta
```

Cada 24DSF nuevo trae 24 meses: se agrega el período nuevo y se **reemplazan** los 23 solapados (el BCRA rectifica), dejando constancia en `_control/ingestas.csv`.

## Fases

| # | Fase | Entregable | Estado |
|---|---|---|---|
| 0 | Carga inicial a S3 | 24 períodos de deudores + v1 + registro de control | hecho (tamaños verificados contra local) |
| 1 | Estudio de datos del 24DSF | [`reportes/estudio_24dsf.md`](reportes/estudio_24dsf.md) | hecho |
| 1b | Fuentes complementarias (mensual, maestro de entidades, 1DSF, rectificativas, padrón ARCA) | [`reportes/fuentes_bcra_arca.md`](reportes/fuentes_bcra_arca.md), [`referencias/`](referencias/) | hecho (subidas a S3, registro en `_control/fuentes.csv`) |
| 2 | Catálogo de variables por bloques | [`catalogo/variables.yaml`](catalogo/variables.yaml) (149 variables, validado por `cendeu.catalogo` y test) | hecho (falta corregir signos esperados, ver Fase 6) |
| 3 | Implementación: SQL generado desde el catálogo, grilla densa CUIT×mes, comando de subida a S3, tests | [`src/cendeu/v2.py`](src/cendeu/v2.py), [`src/cendeu/s3.py`](src/cendeu/s3.py), [`tests/test_v2.py`](tests/test_v2.py) | hecho: base v2 y variables de las 7 fechas (202509–202603) calculadas en local; **falta subirlas a S3** |
| 4 | Documentación de variables (diccionario + documento técnico) | [`docs/diccionario_variables.csv`](docs/diccionario_variables.csv), [`docs/variables.md`](docs/variables.md), generados con `cendeu documentar` | hecho |
| 4b | Informe de variables mix CENDEU + transaccionalidad propia (entidad que informa / que no informa) | [`docs/variables_mix.md`](docs/variables_mix.md) | hecho (diseño; implementar cuando haya datos propios) |
| 5 | Informe de variables y poder (IV, Gini, KS, nulos, PSI, correlaciones, valor incremental vs scorecard v1) | [`reportes/poder_v2/`](reportes/poder_v2/) (`cendeu poder`) y su página en [`reportes/poder_v2/pagina/`](reportes/poder_v2/pagina/), publicada como artifact en claude.ai | hecho: 99 de 139 variables con IV ≥ 0,1, 60 grupos no redundantes, PSI máximo 0,034 |
| 6 | Selección y nuevo scorecard | `modelos/` | pendiente |

## Resultados de la Fase 5 y decisiones para la Fase 6

- **Poder**: calidad, momentum y transiciones concentran el poder (IV máximos 1,5–1,7); velocidad, relación con el sistema y mix por tipo aportan menos IV pero más información distinta (más grupos no redundantes). Todas las variables son estables entre fechas (PSI < 0,04) y su Gini fuera de muestra es igual o mayor al de desarrollo.
- **Incremental vs v1** (Gini v1 0,586 en 202509, humanas en situación 1): la mejor variable sola suma +0,009 (edad), seguida de variación de deuda y RSI de deuda (+0,005). La v1 se entrenó con esa fecha, así que la ganancia está subestimada; el aporte real se mide en el modelo multivariado.
- **Segmentar**: la población incluye situación 2, y muchas variables de magnitud y calidad se reducen a "está en situación 2 ahora". Modelar por separado situación 1 y situación 2.
- **Signos esperados** a corregir en el catálogo: rolls de 2 a 3, meses de mejora, bajas, CV de deuda, huecos y reactivación tienen el signo opuesto al planteado.
- **Validación**: entrenar con 202509–202601 y validar fuera de muestra con 202602–202603.

## Lecciones de la implementación

- **Fotos del corte**: el 1DSF y el padrón son fotos de hoy. El 1DSF filtraba el desempeño (IV 7,5 falso), por eso las variables tienen `disponible_desde` y quedan nulas en fechas anteriores a su fuente. Actividad y provincia del padrón no mostraron señales de fuga (PSI ≈ 0), pero siguen siendo fotos.
- **Borde de la historia**: las variables que necesitan el mes previo a su ventana (`requiere_mes_previo`) quedan nulas cuando ese mes no está en el 24DSF (202509 para ventanas de 12 meses).
- **Memoria**: la base se escribe bucket por bucket y las variables por partes de ~1,3 M de CUITs con 10 GB; con menos memoria fallan pasos que DuckDB no vuelca a disco. Para iterar en desarrollo conviene una muestra fija (20% de los CUITs) y dejar la población completa para scorear en producción.
- **Reproducibilidad**: los montos tienen ruido de punto flotante entre corridas (sumas en paralelo, ≤ 1e-7); los enteros y categóricas son idénticos.

## Pendientes

- Subir a S3 la base v2 y las variables de las 7 fechas (prefijo a definir para la base, p. ej. `base_v2/`).
- Modo muestra para desarrollo en `cendeu variables-v2`.
- Fase 6: segmentos, selección por grupo, scorecard v2 y comparación contra v1.
- Al tener v2: borrar de S3 variables y scores v1 (*fresh start*, con confirmación).
- Limpiar los archivos sueltos sin versionar de la raíz (copias viejas de la v1).
- Merge de `v2/estudio-datos` en `main`.

## Bloques de variables (Fase 2)

1. **Magnitud**: deuda total, promedio, máximo, deuda irregular (situación ≥2) y su peso.
2. **Calidad**: peor situación por ventana, situación ponderada por monto, flag de peor momento.
3. **Transiciones**: rolls 1→2 y 2→3+, tasa de cura (≥2 → 1), recencia, severidad, rachas.
4. **Velocidad**: VAR mes a mes, TEN (actual vs hace N meses), FMX (actual / total ventana), aceleración, STD y CV.
5. **Momentum (RSI)** 3/6/12 sobre deuda, situación, cantidad de entidades y deuda irregular.
6. **Relación con el sistema**: entidades, altas y bajas, principalidad y cambio de principalidad, concentración (HHI).
7. **Mix por tipo de entidad**: entropía de Shannon y participación por tipo (`tipo_agrupado` de `referencias/entidades.csv`).
8. **Foto actual del mensual**: días de atraso, refinanciación, recategorización obligatoria, situación jurídica, garante, deuda con fideicomiso de recupero.
9. **Persona (padrón ARCA y 1DSF)**: edad o antigüedad de la empresa, actividad registrada, provincia, meses en situación normal. Sexo sólo para monitorear sesgos.
10. **Alertas combinadas** (incluye fallecido con deuda).

## Diferencias con el CRC a tener en cuenta

- El 24DSF equivale al *Afectado* del CRC (deuda por entidad): no trae producto, contingencia, codeudoría, deuda vendida, fideicomiso ni depósitos → no aplican saturación de líneas, revolver ni cobertura con depósitos.
- La situación (1–5) reemplaza a días de atraso y clasificación.
- 24 meses de historia (vs 6) y montos deflactados por IPC (vs tipo de cambio fijo).
- Evitar del CRC: SQL repetido a mano, huecos en la serie al calcular diferencias (usar grilla densa), exceso de variables colineales.
