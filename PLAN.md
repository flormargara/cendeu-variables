# Plan v2 — Variables CENDEU

Rediseño de las variables de comportamiento sobre la Central de Deudores (BCRA, archivo `24DSF`), siguiendo el enfoque del Feature Store CRC: variables diseñadas por dominio de riesgo (no por disponibilidad), dinámica temporal (VAR, TEN, FMX, RSI), documentación completa y medición de poder antes de usarlas.

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Nomenclatura | Unificada: `EX_CEN_<OPERACION>_<DESCRIPCION>_<VENTANA>`. No se mantienen los nombres v1. |
| Ventanas | 3, 6 y 12 meses. |
| Target | Situación ≥3 en los **6 meses** siguientes (permite varias fechas de observación, validación *out-of-time* y PSI). Target a 12 meses queda para iteraciones futuras. |
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
  _control/fuentes.csv                        registro de las demás fuentes: archivo de origen, filas, bytes, ruta
```

Cada 24DSF nuevo trae 24 meses: se agrega el período nuevo y se **reemplazan** los 23 solapados (el BCRA rectifica), dejando constancia en `_control/ingestas.csv`.

## Fases

| # | Fase | Entregable | Estado |
|---|---|---|---|
| 0 | Carga inicial a S3 | 24 períodos de deudores + v1 + registro de control | hecho (tamaños verificados contra local) |
| 1 | Estudio de datos del 24DSF | [`reportes/estudio_24dsf.md`](reportes/estudio_24dsf.md) | hecho |
| 1b | Fuentes complementarias (mensual, maestro de entidades, 1DSF, rectificativas, padrón ARCA) | [`reportes/fuentes_bcra_arca.md`](reportes/fuentes_bcra_arca.md), [`referencias/`](referencias/) | hecho (subidas a S3, registro en `_control/fuentes.csv`) |
| 2 | Catálogo de variables por bloques | `catalogo/variables.yaml` | pendiente |
| 3 | Implementación: SQL generado desde el catálogo, grilla densa CUIT×mes, comando de subida a S3, tests | `src/cendeu/` | pendiente |
| 4 | Documentación de variables (diccionario + documento técnico) | `docs/diccionario_variables.csv`, `docs/variables.md` | pendiente |
| 4b | Informe de variables mix CENDEU + transaccionalidad propia (entidad que informa / que no informa) | `docs/variables_mix.md` | pendiente |
| 5 | Informe de variables y poder (IV, Gini, KS, nulos, PSI, correlaciones, valor incremental vs scorecard v1) | informe HTML | pendiente |
| 6 | Selección y nuevo scorecard | `modelos/` | pendiente |

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
