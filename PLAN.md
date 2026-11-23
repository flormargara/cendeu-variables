# Plan v2 — Variables CENDEU

Rediseño de las variables de comportamiento sobre la Central de Deudores (BCRA, archivo `24DSF`). El repo es sólo de variables: los modelos de PD que las usen van en otros proyectos. Se sigue el enfoque del Feature Store CRC: variables diseñadas por dominio de riesgo (no por disponibilidad), dinámica temporal (VAR, TEN, FMX, RSI), documentación completa y medición de poder antes de usarlas.

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Nomenclatura | Unificada: `CEN_<OPERACION>_<DESCRIPCION>_<VENTANA>`. No se mantienen los nombres v1. |
| Ventanas | 3, 6 y 12 meses. |
| Target | Situación ≥3 en los **6 meses** siguientes (permite varias fechas de observación, validación *out-of-time* y PSI). Target a 12 meses queda para iteraciones futuras. |
| IPC | INDEC vía datos.gob.ar, versionado en [`referencias/ipc.csv`](referencias/ipc.csv) y copiado por corte a S3; cada base registra su hash y meses imputados en `_meta.json`. |
| Datos locales | `~/cendeu/datos` (fuentes convertidas, `v2/base`, `v2/variables`) y `~/cendeu/entrada` para los paquetes de ARCA; fuera de `~/Downloads` porque launchd no puede leerlo. |
| Almacenamiento | `s3://data-cendeu-raw` (perfil `fmargara`, política [`infra/cendeu-s3-policy.json`](infra/cendeu-s3-policy.json)), una carpeta por período. |
| v1 | Variables y scores v1 se subieron sólo como línea de base y se borraron de S3 el 2026-11-15, al quedar la v2 completa (*fresh start*, registro en `_control/borrados.csv`). |
| Repo | Se mantiene el repo actual; el rediseño se hace sobre él. |

## Layout S3

```
s3://data-cendeu-raw/
  deudores/periodo=AAAAMM/data.parquet        un mes de la central, todas las entidades
  base/{deudores,mensual,persona}/…           base limpia v2 (24DSF + mensual, pesos constantes), particionada por período y bucket
  variables/periodo_obs=AAAAMM/parte_XX.parquet  variables v2 por CUIT a esa fecha (16 partes)
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
  _control/variables.csv                      una fila por parte de variables: fecha de observación, bytes, ruta, estado
  _control/borrados.csv                       objetos borrados del bucket: fecha, ruta, bytes, motivo
```

Cada 24DSF nuevo trae 24 meses: se agrega el período nuevo y se **reemplazan** los 23 solapados (el BCRA rectifica), dejando constancia en `_control/ingestas.csv`.

## Fases

| # | Fase | Entregable | Estado |
|---|---|---|---|
| 0 | Carga inicial a S3 | 24 períodos de deudores + v1 + registro de control | hecho (tamaños verificados contra local; la v1 se borró el 2026-11-15) |
| 1 | Estudio de datos del 24DSF | [`reportes/estudio_24dsf.md`](reportes/estudio_24dsf.md) | hecho |
| 1b | Fuentes complementarias (mensual, maestro de entidades, 1DSF, rectificativas, padrón ARCA) | [`reportes/fuentes_bcra_arca.md`](reportes/fuentes_bcra_arca.md), [`referencias/`](referencias/) | hecho (subidas a S3, registro en `_control/fuentes.csv`) |
| 2 | Catálogo de variables por bloques | [`catalogo/variables.yaml`](catalogo/variables.yaml) (149 variables, validado por `cendeu.catalogo` y test) | hecho (signos esperados corregidos con la Fase 5 el 2026-11-22; la hipótesis original queda en `observaciones`) |
| 3 | Implementación: SQL generado desde el catálogo, grilla densa CUIT×mes, comando de subida a S3, tests | [`src/cendeu/v2.py`](src/cendeu/v2.py), [`src/cendeu/s3.py`](src/cendeu/s3.py), [`tests/test_v2.py`](tests/test_v2.py) | hecho: base v2 y variables de las 7 fechas (202509–202603) subidas a S3 (417 archivos de base y 112 partes de variables, tamaños verificados; registro en `_control/`) |
| 4 | Documentación de variables (diccionario + documento técnico) | [`docs/diccionario_variables.csv`](docs/diccionario_variables.csv), [`docs/variables.md`](docs/variables.md), generados con `cendeu documentar` | hecho |
| 4b | Informe de variables mix CENDEU + transaccionalidad propia (entidad que informa / que no informa) | [`docs/variables_mix.md`](docs/variables_mix.md) | hecho (diseño; implementar cuando haya datos propios) |
| 5 | Informe de variables y poder (IV, Gini, KS, nulos, PSI, correlaciones, valor incremental vs scorecard v1) | [`reportes/poder_v2/`](reportes/poder_v2/) (`cendeu poder`) y su página en [`reportes/poder_v2/pagina/`](reportes/poder_v2/pagina/), publicada como artifact en claude.ai | hecho: 99 de 139 variables con IV ≥ 0,1, 60 grupos no redundantes, PSI máximo 0,034; repetido por segmento (sit1 y sit2) con selección recomendada en `catalogo/seleccion.csv` |
| 6 | Validación multivariada de las variables (ajuste por segmento sólo como prueba, no como modelo) | [`reportes/validacion_v2/`](reportes/validacion_v2/) (`cendeu validar-v2`) | hecho |
| 7 | Actualización mensual automatizada | `cendeu actualizar` ([`src/cendeu/actualizar.py`](src/cendeu/actualizar.py)), resumen por corte en [`reportes/actualizaciones/`](reportes/actualizaciones/) | etapas 1 y 2 hechas: comando con controles (probado con el corte 202609) y revisión diaria en launchd (`cendeu programar`, instalada el 2026-11-23); opcional: correrlo en AWS |

## Resultados de la Fase 5

- **Poder**: calidad, momentum y transiciones concentran el poder (IV máximos 1,5–1,7); velocidad, relación con el sistema y mix por tipo aportan menos IV pero más información distinta (más grupos no redundantes). Todas las variables son estables entre fechas (PSI < 0,04) y su Gini fuera de muestra es igual o mayor al de desarrollo.
- **Incremental vs v1** (Gini v1 0,586 en 202509, humanas en situación 1): la mejor variable sola suma +0,009 (edad), seguida de variación de deuda y RSI de deuda (+0,005). La v1 se entrenó con esa fecha, así que la ganancia está subestimada; el aporte real se mide en el modelo multivariado.
- **Segmentar**: la población incluye situación 2, y muchas variables de magnitud y calidad se reducen a "está en situación 2 ahora". Para usarlas en un modelo conviene separar situación 1 y situación 2.
- **Signos esperados** (corregidos en el catálogo el 2026-11-22): rolls de 2 a 3, recencia del peor momento, huecos y reactivación pasan a `-`; meses de mejora y bajas a `+`; CV de deuda a `?` (sentido distinto a 6 y 12 meses). En huecos y reactivación el IV es ≈ 0 y el sentido es poco confiable. El `resumen.csv` del informe conserva los signos viejos hasta la próxima corrida de `cendeu poder`.
- **Selección recomendada** (`cendeu seleccion` → [`catalogo/seleccion.csv`](catalogo/seleccion.csv)): 34 variables con uso `modelo`, IV ≥ 0,1, PSI < 0,1 y representantes de su grupo de correlación. No recorta el cálculo: todas se siguen generando, y la marca sólo indica al proyecto de modelos de dónde partir. Se muestra en el diccionario (`IV`, `GRUPO`, `RECOMENDADA`) y en el documento técnico.
- **Poder por segmento** (2026-11-22, `cendeu poder --segmento sit1|sit2`, en [`reportes/poder_v2_sit1/`](reportes/poder_v2_sit1/) y [`reportes/poder_v2_sit2/`](reportes/poder_v2_sit2/); muestras del 10% y del 30%). Situación 1 tiene tasa de target de 7% y 82 variables con IV ≥ 0,1; situación 2, tasa de 78% y 36 con IV ≥ 0,1. La selección por segmento recomienda 33 variables en sit1 y 18 en sit2, y sólo 6 se repiten en ambos. Diez de las 34 recomendadas globales no quedan recomendadas en ningún segmento (recencia de situación 2, peor situación a 6 meses, RSI de situación, alertas de contagio y reincidencia, meses de mejora, entre otras): miden sobre todo "está en situación 2 ahora", y confirman que conviene elegir variables por segmento. Las que mejor separan dentro de los segmentos son las de dinámica (RSI de deuda, cantidad de entidades, altas, FMX) y la deuda no bancaria.
- **Sentido por segmento**: en sit2 se invierten 31 sentidos, casi todos con IV < 0,1. Por ejemplo, más meses en situación 2 se asocian a menos riesgo, porque quien recién cae a situación 2 pasa a 3 más que quien se sostiene ahí. En sit1 se invierte el RSI de situación. El sentido del catálogo sigue siendo el de la población total; el de cada segmento está en su `resumen.csv`.
- **Validación multivariada (Fase 6)**: ver [`reportes/validacion_v2/`](reportes/validacion_v2/).

## Lecciones de la implementación

- **Tamaño de las variables**: el 92% del peso eran las 74 variables decimales guardadas en doble precisión con todos sus decimales (ruido de la deflación, casi incompresible). Desde el 2026-11-23 se guardan redondeadas por unidad (montos a 1 decimal, índices 0-100 a 2, el resto a 4) y en precisión simple, con zstd nivel 9: cada fecha pasa de ~5 GB a ~2,5 GB, sin cambiar enteros ni categóricas (las 8 fechas: 37 → 19 GB). El informe de poder sobre las variables compactas da los mismos signos, grupos y recomendaciones; IV, Gini y PSI se mueven menos de 0,001. El redondeo explica casi todo el ahorro; la precisión simple suma poco. Si alguna vez hicieran falta más decimales, se reprocesa desde los crudos (`~/projects/cendeu-crudos/`).

- **Archivo de control del 24DSF**: trae 25 meses (el del corte 202609 incluye 202409) y en 214 pares entidad × mes duplica cantidad y monto. El control compara sólo los 24 meses del archivo y acepta el duplicado.

- **Fotos del corte**: el 1DSF y el padrón son fotos de hoy. El 1DSF filtraba el desempeño (IV 7,5 falso), por eso las variables tienen `disponible_desde` y quedan nulas en fechas anteriores a su fuente. Actividad y provincia del padrón no mostraron señales de fuga (PSI ≈ 0), pero siguen siendo fotos.
- **Borde de la historia**: las variables que necesitan el mes previo a su ventana (`requiere_mes_previo`) quedan nulas cuando ese mes no está en el 24DSF (202509 para ventanas de 12 meses).
- **Memoria**: la base se escribe bucket por bucket y las variables por partes de ~1,3 M de CUITs con 10 GB; con menos memoria fallan pasos que DuckDB no vuelca a disco. Para iterar en desarrollo está el modo muestra (`variables-v2 --pct-muestra 30`, ver README) y la población completa queda para la corrida que se sube a S3.
- **Fechas que arma cada corte** (decidido el 2026-11-23): cada corte sólo arma las fechas nuevas; `--recalcular` (~1,5 h más) se usa a mano, por ejemplo antes de reentrenar un modelo, para que las fechas viejas no cambien solas. Corregido el mismo día: la fecha corte − 6 se rehace siempre, aunque exista, para que la foto armada seis cortes atrás reciba su target (antes quedaba sin target para siempre).
- **Reproducibilidad**: los montos tienen ruido de punto flotante entre corridas (sumas en paralelo, ≤ 1e-7); los enteros y categóricas son idénticos.

## Pendientes

- Cuando exista el proyecto de modelos: pasar allí `scorecard.py`, `scoring.py` y el scorecard v1.
- Corte 202610: bajar los seis paquetes de ARCA a `~/cendeu/entrada` cuando estén (el de 202609 se procesó el 2026-11-05, así que se espera a principios de diciembre); la revisión diaria hace el resto.
- Actualización mensual, etapa 3 (opcional): correrla en AWS con el rol `data-cendeu`, disparada al llegar los paquetes a `s3://data-cendeu-raw/entrada/AAAAMM/`.

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
