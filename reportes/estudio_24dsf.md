# Estudio de datos — 24DSF (corte 202609)

Fase 1 del [plan v2](../PLAN.md). Objetivo: entender la calidad y la estructura del bianual de la Central de Deudores antes de diseñar variables, al estilo del *Estudio CRC Afectado*.

- **Fuente**: Parquet largo generado por `cendeu convertir` (una fila por CUIT × entidad × período), 24 períodos de 202410 a 202609, 902.235.249 filas.
- **Script**: [`estudios/estudio_24dsf.py`](../estudios/estudio_24dsf.py). Tablas agregadas en [`reportes/estudio_24dsf/`](estudio_24dsf/) (sin datos personales).
- **Montos**: miles de pesos; "real" = pesos constantes de 202609 deflactados por IPC.

## Resumen de hallazgos

| # | Hallazgo | Impacto en el diseño |
|---|---|---|
| 1 | **202609 está incompleto**: faltan 68 entidades que informaron en 202608 (2,9% de las filas, 0,5% del monto; la mayor con 306 mil filas). | Último mes no confiable para altas/bajas y deuda. Los CUITs de esas entidades parecen "cancelar" sin hacerlo. |
| 2 | **La irregularidad crece fuerte en la ventana**: filas en situación ≥2 pasan de 13% a 28%; monto en situación ≥2 de 3% a 14%. | Tasa de default distinta por fecha de observación → medir estabilidad (PSI) y calibrar por cohorte. |
| 3 | **5 fusiones/migraciones de cartera** detectadas: entre 84% y 90% de los clientes de la entidad que deja de informar aparecen al mes siguiente en una misma entidad nueva para ellos. | Sin corrección, generan "altas" y "bajas" falsas y cambios de principalidad artificiales. Hace falta un mapa de equivalencias de entidades. |
| 4 | **21% de los CUITs tiene huecos** en su serie (aparece, desaparece, reaparece). | Las diferencias mes a mes y el RSI tienen que calcularse sobre una **grilla densa** CUIT × mes (ausente = sin deuda informada). |
| 5 | **Roll rates altos**: de situación 2, el 34% pasa a 3 al mes siguiente y sólo el 17% se cura; de 3, el 29% pasa a 4. | Las transiciones (bloque 3) son una señal fuerte; la situación 2 es el estado clave para alertas tempranas. |
| 6 | **Entradas directas en situación 5**: 514 mil apariciones de CUITs que entran a la central ya en situación 5 (3,3% de las altas). | Probable compra/cesión de cartera morosa: variable análoga a "cartera vendida" del CRC. |
| 7 | El campo `flag` es **Proceso judicial/Revisión** según el LEAME (0 = no observado, 1 = proceso judicial, 2 = en revisión, Ley 25.326). Sólo 460 filas en 1 sobre 902 M, ninguna en 2. | Poder casi nulo por frecuencia; se conserva como flag y se reevalúa con el informe de poder. |
| 8 | Las situaciones **6 y 11 no aparecen**. | Se simplifica el tratamiento de situación (1 a 5). |
| 9 | **Duplicados** CUIT × entidad × período despreciables (~100 filas por mes). | Se mantiene la regla actual: peor situación y suma de montos. |
| 10 | 101 filas con `tipo_id` 98/99 y CUIT inválido. | Se excluyen. |

## 1. Volumen por período

| Período | Filas (M) | CUITs (M) | Entidades | Monto real (billones $ de 202609) | % filas sit ≥2 | % monto sit ≥2 |
|---|---|---|---|---|---|---|
| 202410 | 31,6 | 18,2 | 470 | 128,0 | 13,1% | 3,3% |
| 202501 | 33,5 | 18,9 | 469 | 150,0 | 14,4% | 3,9% |
| 202504 | 35,4 | 19,4 | 461 | 164,3 | 16,4% | 5,5% |
| 202507 | 37,5 | 20,3 | 486 | 186,9 | 19,4% | 7,1% |
| 202510 | 38,6 | 20,6 | 493 | 194,7 | 22,4% | 9,5% |
| 202601 | 39,6 | 20,8 | 510 | 196,1 | 25,2% | 11,7% |
| 202604 | 40,5 | 21,0 | 510 | 191,2 | 27,1% | 13,3% |
| 202607 | 41,1 | 21,3 | 530 | 197,3 | 27,9% | 13,8% |
| 202608 | 41,8 | 21,5 | 543 | 199,3 | 28,0% | 14,0% |
| 202609 | 41,1 | 21,3 | **476** | 204,3 | 26,4% | 13,6% |

- La deuda real crece 60% en dos años, casi toda entre 202410 y 202510; desde entonces está estable.
- La caída de filas y de % irregular en 202609 es consistente con entidades faltantes (hallazgo 1), no con una mejora real.
- No hay montos en cero ni negativos (el convertidor sólo guarda filas con situación > 0).

## 2. Entidades

- **Continuidad de reporte**: 271 entidades informan los 24 meses y concentran el 94,5% de las filas. 316 informan de forma intermitente (183 entre 18 y 23 meses, 104 entre 6 y 17, 29 menos de 6).
- **202609**: 68 entidades presentes en 202608 no informan. En la próxima corrida mensual hay que comparar contra el 24DSF siguiente: si aparecen con 202609, el corte actual tenía un retraso de envío.
- **Bancos vs resto** (202608): 60 entidades con código < 1000 tienen el 48% de las filas; 483 entidades con códigos 10000+ el 52%. Los códigos se agrupan por rangos (55xxx: 312 entidades; 70xxx y 72xxx: 79 entidades con el 31% de las filas), pero **para tipificarlas hace falta la tabla de entidades del BCRA**.

### Fusiones y migraciones de cartera

| Entidad que deja de informar | Último mes | Entidad destino | CUITs que migran | % de su cartera |
|---|---|---|---|---|
| 259 | 202410 | 285 | 131.519 | 88% |
| 55385 | 202410 | 72632 | 12.157 | 84% |
| 70218 | 202503 | 55514 | 42.561 | 90% |
| 150 | 202505 | 7 | 314.154 | 85% |
| 70116 | 202509 | 55547 | 4.810 | 90% |

Criterio: la entidad no informa al mes siguiente, tenía ≥100 CUITs y ≥30% de ellos aparece en una misma entidad con la que no operaban. Los casos 259→285 y 150→7 son consistentes con adquisiciones bancarias conocidas del período; conviene confirmarlos con la tabla de entidades.

Según el LEAME, el 24DSF **sólo incluye deudores de entidades activas y, de las entidades dadas de baja, únicamente los registros en situación normal**. Para una entidad absorbida, la historia en situación 2+ desaparece del archivo: la deuda irregular previa a la fusión no se ve bajo el código viejo.

**Tratamiento propuesto**: tabla `entidades_equivalencias` (entidad origen → destino, desde período). Para las variables de relación con el sistema (altas, bajas, principalidad) se reemplaza el código de origen por el de destino en toda la historia.

## 3. CUITs

- **Tipo de persona** (202609): 21,06 M humanas (98,7%) y 287 mil jurídicas (1,3%).
- **Entidades por CUIT** (202609): 49% con una sola, 27% con dos, 13% con tres, 11% con cuatro o más.
- **Continuidad de la serie**:

| Serie | Vigente en el último mes | CUITs (M) | Meses promedio presentes |
|---|---|---|---|
| Sin huecos | Sí | 17,39 | 22,0 |
| Sin huecos | No (salió) | 2,63 | 9,4 |
| Con huecos | Sí | 3,95 | 16,2 |
| Con huecos | No | 1,25 | 10,4 |

## 4. Transiciones de situación (mes a mes, peor situación del CUIT)

Proporción sobre el estado del mes *t*; **0** = no figura en la central (sin deuda informada).

| Desde \ Hacia | 0 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| **0** (alta) | — | 94,5% | 1,5% | 0,5% | 0,3% | **3,3%** |
| **1** | 2,9% | 94,4% | 2,6% | 0,04% | 0,02% | 0,05% |
| **2** | 2,0% | **17,3%** | 46,1% | **34,2%** | 0,3% | 0,1% |
| **3** | 1,6% | 3,7% | 2,2% | 63,2% | **29,1%** | 0,3% |
| **4** | 1,3% | 1,5% | 0,4% | 0,6% | 79,5% | **16,7%** |
| **5** | 2,4% | 0,9% | 0,1% | 0,1% | 0,4% | 96,1% |

- La situación 2 es inestable: en un mes se resuelve (cura o empeora) más de la mitad.
- Las "curas" desde 3+ son bajas (≤4%) y desde 5 casi inexistentes; las salidas desde 5 (2,4%) son probablemente pases a cuentas de orden o ventas de cartera, no pagos.
- Base para el bloque 3: tasas de roll y de cura **individuales** sobre la historia de 12 meses de cada CUIT.

## 5. Montos (202609, miles de $ nominales)

| Situación | Mediana | p90 | p99 | Máximo |
|---|---|---|---|---|
| 1 | 660 | 5.003 | 41.638 | 847.637.601 |
| 2 | 723 | 6.755 | 36.675 | 108.972.913 |
| 3 | 631 | 5.625 | 29.481 | 110.913.135 |
| 4 | 683 | 5.656 | 29.413 | 98.393.496 |
| 5 | 474 | 3.680 | 19.339 | 16.677.040 |

Colas muy largas (máximos de grandes empresas): usar ratios, logaritmos o binning; evitar promedios simples sin acotar.

## Decisiones para las fases 2 y 3

1. **Último mes válido**: hasta confirmar 202609, las fechas de observación para modelar van de 202509 a 202603 (12 meses de historia + 6 de desempeño, terminando en 202609). En el target a 6 meses, la falta de entidades en 202609 puede subestimar levemente el default del último mes de desempeño: se medirá con y sin 202609.
2. **Grilla densa** CUIT × mes para todas las variables temporales.
3. **Mapa de equivalencias de entidades** para neutralizar fusiones.
4. **Conseguir la tabla de entidades BCRA** (nombre y tipo) para el bloque de mix por tipo de entidad.
5. Descartar `tipo_id` ≠ 11 y tratar situación en escala 1–5; `flag` se renombra como proceso judicial.
8. **Otras fuentes del mismo paquete del BCRA** (ver LEAME): `maeent.txt` (maestro de entidades), `1DSF.txt` (fecha desde la cual el deudor está en situación 1 ininterrumpida, sin el tope de 24 meses) y el mensual `deudores.txt`, que agrega días de atraso, refinanciaciones, recategorización obligatoria, situación jurídica, garantías y previsiones.
6. Nueva variable: **aparición directa en situación ≥4** (proxy de cartera cedida o comprada).
7. Medir **PSI por fecha de observación** en el informe de poder, dada la tendencia de deterioro del sistema.
