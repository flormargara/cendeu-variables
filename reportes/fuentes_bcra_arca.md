# Fuentes complementarias BCRA y ARCA (corte 202609)

Complemento de la Fase 1 ([estudio del 24DSF](estudio_24dsf.md)). Revisa los paquetes mensuales que acompañan al bianual y el padrón de ARCA: qué traen, si coinciden con su diseño de registro y qué aportan a las variables.

- **Conversión**: [`src/cendeu/paquete.py`](../src/cendeu/paquete.py) (ancho fijo → Parquet, valida el largo de cada registro contra el LEAME antes de convertir).
- **Referencias generadas** (públicas, sin datos personales): [`referencias/entidades.csv`](../referencias/entidades.csv) y [`referencias/entidades_equivalencias.csv`](../referencias/entidades_equivalencias.csv).

## Inventario

| Paquete | Archivo | Registros | Largo (LEAME) | Contenido | Uso |
|---|---|---|---|---|---|
| 202609DEUDORES | `deudores.txt` | 42,3 M | 171 ✓ | Central mensual completa: 24 campos por CUIT × entidad | Completa el 202609 del 24DSF y suma campos nuevos |
| 202609DEUDORES | `Maeent.txt` | 580 | 75 ✓ | Maestro de entidades (código, nombre) | Tipo de entidad, confirmación de fusiones |
| 202609DEUDORES | `Nomdeu.txt` | — | 77 ✓ | Nombres de deudores no empadronados | No se usa (sólo nombres) |
| 1DSF202609 | `1DSF.txt` | 14,9 M | 19 ✓ | Desde cuándo el deudor está en situación 1 en todas las entidades, sin tope de 24 meses | Antigüedad en situación normal |
| 202609INFRET | `inf_ret.txt` | 3.563 | 93 ✓ | Rectificativas completas publicadas en el mes | Control de reproceso |
| 202609INFRETPA | `inf_retparc.txt` | 1,87 M | 93 ✓ | Rectificativas parciales (históricas, 2001–2026) | Control de reproceso |
| 20261030PADRON | `Padron_ARCA.txt` | 65,5 M | 220 ✓ | Padrón ARCA: actividad, nacimiento/contrato social, sexo, CP, provincia, fallecimiento, bajas | Atributos de la persona |
| 20261030PADRON | `Actividades_ARCA.txt` | 2.327 | **266** (LEAME dice 260) | Nomenclador de actividades | Descripción de actividad |
| 20261031CHEQUES | `cheques.txt` | 3,78 M | 81 ✓ | Central de Cheques Rechazados: foto completa al día de la descarga | Bloque B11 (cheques propios y de empresas vinculadas) |

Notas de formato: todos los archivos vienen en latin-1 con fin de línea CRLF. `inf_retparc.txt` y `Padron_ARCA.txt` traen algunos bytes de control dentro de los nombres; el conversor los reemplaza (los nombres no se guardan). En `Actividades_ARCA.txt` la descripción mide 260 y no 254 como dice el LEAME.

## 1. El mensual completa el último mes del 24DSF

| | 24DSF (mes 202609) | `deudores.txt` 202609 |
|---|---|---|
| Filas | 41.109.497 | 42.315.138 |
| CUITs | 21.345.390 | 21.694.648 |
| Entidades | 476 | 545 |

- En las **476 entidades comunes** filas y montos coinciden exactamente (el monto del 24DSF = préstamos + garantías otorgadas + otros conceptos del mensual).
- Las **69 entidades restantes** (68 de ellas informaron en 202608) están en el mensual: 1,2 M filas y ~1 billón de pesos. El 24DSF las omite en su primer mes; no es un problema de envío de las entidades.
- **Regla de ingesta**: cada mes se toma el período de corte desde el mensual y los 23 anteriores desde el 24DSF.
- **Situación 11** (asistencias cubiertas con garantía preferida "A"): 7.849 filas en el mensual que el 24DSF muestra como situación 1. Se tratan como 1.

## 2. Campos nuevos del mensual

Cobertura por tipo de entidad (202609, proporción de filas):

| Tipo | Filas (M) | Días de atraso > 0 (sit ≥2) | Refinanciación | Recategorización obligatoria | Situación jurídica | Con garantía preferida |
|---|---|---|---|---|---|---|
| Banco | 20,5 | 11,4% | 2,0% | 1,5% | 0,34% | 2,4% |
| PNFC | 13,2 | 0,8% | n/a (9) | 2,4% | n/a | 0% |
| Tarjeta no bancaria | 5,6 | 0,5% | n/a (9) | 1,2% | n/a | 0% |
| Compañía financiera | 2,3 | 10,6% | 4,6% | 0,5% | 0,35% | 8,3% |
| Fideicomiso | 0,7 | 0% | 0,04% | 0% | 0,01% | 0% |

- **Días de atraso**: lo informan 162 de 545 entidades y casi sólo para situación ≥2. Cuando está, es consistente con la situación (mediana 51 días en sit 2, 76 en 3, 144 en 4, 347 en 5). Útil como intensidad dentro de la situación, no como reemplazo.
- **Refinanciación y situación jurídica**: sólo entidades financieras (bancos y compañías financieras); para PNFC, tarjetas y SGR vienen en 9 = no aplica.
- **Recategorización obligatoria**: informada por todos los tipos (1–2,4%). Señal directa de deterioro detectado en el sistema.
- **Previsiones**: siempre 0 en el archivo publicado; no se difunden.
- **Garantías otorgadas** (el deudor como garante): 33.606 filas. Es la única aproximación a codeudoría del CRC.
- **Limitación**: estos campos existen **sólo en el mensual**. Para tener historia hay que acumular mensuales hacia adelante; hoy hay un único mes (202609), así que sirven como variables de foto actual, no de tendencia.

## 3. Entidades

- `Maeent.txt` cubre 577 de las 587 entidades del 24DSF. Las 10 que faltan son fideicomisos financieros (código 10xxx) que dejaron de informar antes de 202602 (66 mil filas en total).
- **Tipificación por rango de código**, verificada con los nombres:

| Rango | Tipo | Agrupado | Entidades | Activas 202609 |
|---|---|---|---|---|
| < 1000 | Banco | BANCO | 62 | 60 |
| 44xxx, 45xxx, 65xxx | Compañía financiera | CIA_FIN | 13 | 13 |
| 70xxx, 71xxx | Emisora no bancaria de tarjetas (Naranja, Amex, Cencosud, YPF…) | TARJETA_NB | 54 | 45 |
| 55xxx | Proveedor no financiero de crédito (registro) | PNFC | 335 | 270 |
| 72xxx | Otros proveedores no financieros (mutuales, cooperativas, Mercado Libre…) | PNFC | 42 | 32 |
| 10xxx | Fideicomiso financiero (varios de recupero de cartera) | FIDEICOMISO | 25 | 5 |
| 50xxx / 51xxx | SGR / fondo de garantía público | GARANTIA | 58 | 50 |
| 40xxx | Plataforma de préstamos entre particulares | P2P | 1 | 1 |

  La separación entre 55xxx y 72xxx es inferida (los dos son proveedores no financieros); se agrupan juntos.
- **Fusiones confirmadas**: las 5 detectadas en el estudio corresponden a dos absorciones bancarias (BMA → Macro, GGAL → Galicia) y a tres recodificaciones de la misma empresa. El maestro revela 3 casos más con el mismo nombre en dos o tres códigos. Tabla final: [`referencias/entidades_equivalencias.csv`](../referencias/entidades_equivalencias.csv).
- **Fideicomisos de recupero** ("Asset Recovery", "Recupero de Activos", "Gestión de Activos"): explican parte de las entradas directas en situación 5 del estudio (hallazgo 6). Variable candidata: tener deuda con un fideicomiso de recupero.

## 4. 1DSF: antigüedad en situación normal

- 14,9 M CUITs (los que hoy están en situación 1 en todas las entidades). Fecha de origen desde 199709 hasta 202609.
- Rompe el tope de 24 meses del bianual: el 43% tiene situación normal ininterrumpida desde antes de 2020.
- Variable directa: **meses en situación normal ininterrumpida** (nula para quien hoy tiene alguna situación ≥2). Como el archivo es una foto, para fechas de observación pasadas hay que reconstruirla: `desde_sit1` sirve si es anterior a la fecha de observación, y si no, se calcula con el 24DSF.

## 5. Rectificativas

- `inf_ret.txt` (202609): 3.563 registros de 4 entidades, todos eliminaciones (situación y monto en 0).
- `inf_retparc.txt`: 1,87 M rectificaciones parciales de períodos 2001–2026; 82% son eliminaciones. 240 mil afectan a 2025–2026, dentro de la ventana de variables.
- **Implicancia**: un mismo período puede cambiar entre cortes. Refuerza la regla de reemplazar los 23 meses solapados en cada ingesta, y conviene guardar las rectificativas de cada mes para poder explicar diferencias.

## 6. Padrón ARCA

65,5 M CUITs/CUILs (toda la población inscripta, no sólo deudores). Cruce con los 21,7 M deudores de 202609:

| | Humanas | Jurídicas |
|---|---|---|
| CUITs en la central | 21.406.261 | 288.387 |
| Encontrados en el padrón | 99,86% | 100% |
| Con fecha de nacimiento / contrato | 99,7% | 98,5% |
| Con actividad registrada en ARCA | 43,3% | 99,9% |
| CUIT dado de baja | 148.670 | 11.216 |
| — con CUIT de reemplazo | 13.805 | 2 |
| **Fallecidos con deuda vigente** | **218.799** | — |

Irregularidad **actual** (situación ≥3 en 202609, no es el target) por atributo, personas humanas:

| Edad | CUITs (M) | % sit ≥3 |
|---|---|---|
| 18–24 | 1,39 | 38,3% |
| 25–34 | 4,50 | 37,5% |
| 35–44 | 4,83 | 32,0% |
| 45–54 | 4,17 | 25,9% |
| 55–64 | 2,91 | 21,1% |
| 65–74 | 2,25 | 17,9% |
| 75+ | 1,29 | 16,1% |

- **Actividad registrada en ARCA** (monotributo, autónomo, empleador): 22,7% en sit ≥3 contra 32,8% sin actividad.
- **Fallecidos**: 79% en sit ≥3. Se excluyen de la población de modelado y quedan como alerta (deuda de sucesión). La fecha 19010101 indica sucesión sin fecha real.
- **CUIT de reemplazo**: el padrón dice qué CUIT reemplaza a uno dado de baja (erróneo o provisorio). Sirve para unir la historia de una misma persona bajo dos números.
- **Sexo**: disponible, pero **no se propone como variable del score** (riesgo de discriminación y de cuestionamiento regulatorio). Se usa sólo para monitorear sesgos del modelo.
- **Código postal y provincia**: variables geográficas candidatas (a nivel provincia o agregado de CP, nunca dirección).
- **Limitación**: el padrón es una foto (20261030). La edad se recalcula a cada fecha de observación sin problema, pero actividad, baja y fallecimiento son de hoy: para fechas pasadas pueden filtrar información del futuro. Fallecimiento sí se puede usar con su fecha.

## 7. Central de Cheques Rechazados

Se baja del mismo servicio de ARCA (*BCRA – Cheques y Deudores* → *Cheques*). A diferencia de los paquetes de deudores, no es un período sino una **foto** de la central el día de la descarga (`AAAAMMDDCHEQUES`); la primera es la del 20261031. Diseño de registro: `LEAME CHEQUES.pdf` (10 campos, 81 caracteres; monto con dos decimales implícitos, fechas AAAAMMDD).

| | Foto 20261031 |
|---|---|
| Filas | 3.784.139 (2.353.876 cheques distintos) |
| CUITs | 220.829 (178.717 en la central de 202609) |
| Rechazos | 2022-01-03 a 2026-10-30; 98,7% sin fondos, 1,3% por vicios formales |
| Levantados | 595.306 filas con fecha de levantamiento, siempre posterior al rechazo |
| Multa | 51% pagada (con fecha), 49% impaga, 0,4% suspendida (Ley 24.452) |
| Revisión / proceso judicial | casi todo en "fin de revisión" y "fin de proceso judicial" (96 filas en curso) |
| Filas de personas vinculadas | 1,38 M: el cheque de una persona jurídica repetido en cada persona relacionada (65.812 CUITs, 54.915 empresas) |
| En la central con rechazo sin fondos en 12 meses | 71.385 CUITs (0,3% de la central) |

- **Sesgo de supervivencia**: entre los rechazos de 2022 figura levantado el 1,6%; entre los de 2026, cerca del 24%. El BCRA da de baja los cheques levantados después de un tiempo, así que una foto no permite reconstruir el estado de la central en fechas pasadas: lo que sobrevive de un año viejo son sobre todo los impagos. Por eso cada foto se usa sólo para el mes que cubre.
- **Reconstrucción al fin de mes**: dentro de una foto sí se puede volver unos días atrás con las fechas de rechazo, levantamiento y pago de multa. Cada mes usa la foto más cercana tomada entre su último día y 45 días después (`v2.fotos_cheques`); lo único que no se recupera son los cheques dados de baja en ese lapso.
- **Historia**: sólo hay una foto. Hay que bajar una por mes (idealmente el último día del mes, o junto con el corte de deudores); las variables quedan con `uso: futuro` hasta que haya target para medir su poder (primera fecha 202610, con desempeño completo en el corte 202704).

## Decisiones

1. **Ingesta mensual**: período de corte desde `deudores.txt` + 23 meses desde el 24DSF; guardar mensual completo, maestro de entidades, 1DSF y rectificativas por período en S3.
2. **Bloque 7 (mix por tipo de entidad)** habilitado con `tipo_agrupado`.
3. **Equivalencias de entidades** aplicadas a variables de relación con el sistema.
4. **Variables nuevas de foto actual**: días de atraso máximo, refinanciación, recategorización obligatoria, situación jurídica, garante (garantías otorgadas), deuda con fideicomiso de recupero, meses en situación normal (1DSF).
5. **Acumular mensuales** desde ahora para que esos campos tengan historia en iteraciones futuras.
6. **Variables de persona (padrón)**: edad o antigüedad de la empresa, actividad registrada y su rubro, provincia. Fallecidos fuera de la población. Sexo sólo para monitorear sesgos.
7. **Unificar CUITs dados de baja** con su CUIT de reemplazo antes de construir variables.
8. **Cheques rechazados** (agregado el 2026-11-25): una foto por mes como séptimo paquete del corte; variables del bloque B11 desde 202610, nulas en los meses sin foto.
