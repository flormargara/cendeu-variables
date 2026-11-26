# Variables CENDEU v2 — documento técnico

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


- **`CEN_TGT_SIT3P_6M`**: Llega a situación 3 o peor en alguna entidad en los 6 meses posteriores a la observación. Definición: `max(SIT) en t+1..t+6 >= 3`.
- **Población**: CUITs presentes en el mes de observación con SIT <= 2, tipo_id 11, no fallecidos a la fecha de observación.
- Ausente en el desempeño = sin deuda informada (no es default). Target a 12 meses (CEN_TGT_SIT3P_12M) queda para iteraciones futuras.

## Convenciones

- **grilla**: Todas las series se calculan sobre una grilla densa CUIT × mes: un CUIT que no figura en un mes tiene deuda 0, situación 0 y 0 entidades. Así las diferencias, ratios temporales y RSI no saltan meses (error del Feature Store CRC).
- **montos**: Miles de pesos constantes de un mes base fijo (default 202609), deflactados por IPC INDEC. Los ratios no dependen de la base.
- **entidades**: Se aplica referencias/entidades_equivalencias.csv antes de contar entidades, altas, bajas y principalidad (las fusiones no generan relaciones nuevas falsas).
- **cuits**: Sólo tipo_id = 11. CUITs dados de baja en el padrón se reemplazan por su CUIT de reemplazo.
- **situacion**: Escala 1–5 (la 11 del mensual se trata como 1); 0 = ausente de la central.
- **historia**: El 24DSF cubre 24 meses. Para la primera fecha de observación de modelado (202509) hay exactamente 12 meses de historia, por eso la ventana máxima es 12M.
- **division_por_cero**: Los ratios con denominador 0 son nulos, nunca 0 ni infinito.
- **precision**: Las variables DECIMAL se guardan redondeadas y en precisión simple (FLOAT): montos a 1 decimal (100 pesos), índices 0-100 a 2 decimales y el resto a 4 (ver decimales_por_unidad; una variable puede fijar `decimales`). Los decimales de más eran ruido de la deflación y ocupaban la mitad del archivo; los cálculos internos se hacen en precisión doble y sólo se redondea al guardar.

## Fuentes

| Código | Fuente |
|---|---|
| `24DSF` | Bianual de la Central de Deudores (24 meses, situación y monto por CUIT × entidad) |
| `MENSUAL` | deudores.txt mensual (24 campos; sólo existe desde 202609, hay que acumularlo) |
| `1DSF` | Fecha desde la cual el deudor está en situación 1 en todas las entidades (sin tope de 24 meses) |
| `MAEENT` | Maestro de entidades del BCRA + referencias/entidades.csv |
| `PADRON` | Padrón de ARCA (foto, corte 20261030) |
| `CHEQUES` | Central de Cheques Rechazados del BCRA (foto completa al día de la descarga, AAAAMMDDCHEQUES; se acumula una por mes desde 20261031) |

## Series mensuales base

| Serie | Contenido |
|---|---|
| `DEUDA` | Deuda total (todas las entidades) |
| `DEUDA_IRR` | Deuda en situación 2 o peor |
| `DEUDA_MORA` | Deuda en situación 3 o peor |
| `SIT` | Peor situación entre entidades (0 = ausente) |
| `SIT_POND` | Situación promedio ponderada por monto (nula si ausente) |
| `ENT` | Cantidad de entidades |
| `ENT_IRR` | Cantidad de entidades con situación 2 o peor |
| `PCT_IRR` | Proporción de la deuda en situación 2 o peor (nula si deuda 0) |
| `HHI` | Concentración de la deuda entre entidades, suma de participaciones al cuadrado |
| `ENT_PRINC` | Entidad con mayor deuda (desempate por menor código) |

## Operaciones

| Código | Significado |
|---|---|
| `MTO` | Monto en el mes |
| `SUM` | Suma en la ventana |
| `AVG` | Promedio en la ventana |
| `MAX` | Máximo en la ventana |
| `MIN` | Mínimo en la ventana |
| `STD` | Desvío estándar en la ventana |
| `CV` | Coeficiente de variación (desvío / promedio) en la ventana |
| `CNT` | Conteo |
| `PCT` | Proporción (0 a 1) |
| `RAT` | Ratio entre dos magnitudes |
| `FLG` | Indicador 0/1 |
| `CAT` | Categoría |
| `MOD` | Moda en la ventana |
| `RCM` | Recencia, meses desde un evento (0 = ocurre en el mes de observación) |
| `VAR` | Variación relativa mes a mes, (x_t - x_{t-1}) / x_{t-1} |
| `TEN` | Tendencia, x_t / x_{t-n+1} (actual contra el inicio de la ventana) |
| `FMX` | Fracción del total, x_t / suma de la ventana |
| `DIF` | Diferencia absoluta, x_t - x_{t-n+1} |
| `PND` | Pendiente de la regresión lineal de x sobre el mes, dividida por el promedio |
| `RSI` | Relative Strength Index, 100 - 100 / (1 + subas / bajas) sobre las variaciones mensuales de la ventana |
| `IDX` | Índice compuesto (HHI, entropía) |
| `VAL` | Valor de un atributo de la persona a la fecha de observación |

## Resumen

165 variables.

| Bloque | Qué mide | Variables | Uso |
|---|---|---|---|
| [B01_MAGNITUD](#b01_magnitud) | Cuánto debe y cuánto de eso es irregular | 19 | modelo: 19 |
| [B02_CALIDAD](#b02_calidad) | Qué tan mala es y fue su situación | 21 | modelo: 21 |
| [B03_TRANSICIONES](#b03_transiciones) | Cómo se mueve entre situaciones (rolls, curas, rachas) | 12 | modelo: 12 |
| [B04_VELOCIDAD](#b04_velocidad) | A qué ritmo cambian deuda, irregularidad y relaciones | 25 | modelo: 25 |
| [B05_MOMENTUM](#b05_momentum) | RSI, fuerza de la tendencia reciente | 12 | modelo: 12 |
| [B06_RELACION_SISTEMA](#b06_relacion_sistema) | Con cuántas y cuáles entidades opera, altas, bajas, principalidad, concentración | 21 | modelo: 21 |
| [B07_MIX_ENTIDADES](#b07_mix_entidades) | Con qué tipo de entidades se endeuda | 16 | modelo: 16 |
| [B08_FOTO_MENSUAL](#b08_foto_mensual) | Campos que sólo trae el mensual (atraso, refinanciación, garante) | 7 | futuro: 7 |
| [B09_PERSONA](#b09_persona) | Atributos de la persona (padrón, 1DSF) | 10 | exclusion: 1, futuro: 1, modelo: 7, monitoreo: 1 |
| [B10_ALERTAS](#b10_alertas) | Combinaciones de señales tempranas | 6 | modelo: 6 |
| [B11_CHEQUES](#b11_cheques) | Cheques rechazados propios y de las empresas vinculadas | 16 | futuro: 16 |

`uso`: **modelo** = candidata al score; **futuro** = sólo existe desde 202609 (mensual), entra cuando haya historia acumulada; **monitoreo** = no entra al score, sirve para medir sesgos; **exclusion** = define la población.

Sentido esperado: relación con el riesgo (`+` = más valor, más riesgo). Se planteó como hipótesis y se corrigió con el informe de poder v2 donde salió al revés (la hipótesis original queda en las notas).

## Variables recomendadas

34 variables marcadas como punto de partida para un modelo (columna **Rec.**): uso `modelo`, IV ≥ 0,1, PSI máximo < 0,1 y representante de su grupo de correlación (|Spearman| ≥ 0,8, la de mayor IV representa al grupo). Es una recomendación, no un recorte: todas las variables del catálogo se siguen calculando, y la elección final depende del modelo que las use (segmento, objetivo). Detalle y motivo de las no recomendadas en [`catalogo/seleccion.csv`](../catalogo/seleccion.csv); métricas en el [informe de poder](../reportes/poder_v2/).

Como la población mezcla situación 1 y situación 2, muchas variables de magnitud y calidad separan sobre todo esos dos grupos. Por eso la selección se repite dentro de cada segmento, con el mismo criterio y su propio informe de poder ([situación 1](../reportes/poder_v2_sit1/), [situación 2](../reportes/poder_v2_sit2/)): **33** recomendadas en sit1, **18** recomendadas en sit2 (columnas **S1** y **S2**).

<a id="b01_magnitud"></a>
## B01_MAGNITUD — Cuánto debe y cuánto de eso es irregular

| Variable | Descripción | Definición | Nulos | Sentido | Uso | IV | Rec. | S1 | S2 |
|---|---|---|---|---|---|---|---|---|---|
| `CEN_MTO_DEUDA_1M` | Deuda total (todas las entidades) en el mes de observación | DEUDA_t | nunca (0 si no tiene deuda de ese tipo) | + | modelo | 0,12 | SI | NO | NO |
| `CEN_MTO_DEUDA_IRR_1M` | Deuda en situación 2 o peor en el mes de observación | DEUDA_IRR_t | nunca (0 si no tiene deuda de ese tipo) | + | modelo | 1,53 | NO | NO | NO |
| `CEN_MTO_DEUDA_MORA_1M` | Deuda en situación 3 o peor en el mes de observación | DEUDA_MORA_t | nunca (0 si no tiene deuda de ese tipo) | + | modelo | 0,00 | NO | NO | NO |
| `CEN_AVG_DEUDA_3M` | Promedio de la deuda total en los últimos 3 meses | AVG(DEUDA) sobre t-2..t | nunca | + | modelo | 0,12 | NO | NO | NO |
| `CEN_AVG_DEUDA_6M` | Promedio de la deuda total en los últimos 6 meses | AVG(DEUDA) sobre t-5..t | nunca | + | modelo | 0,11 | NO | NO | NO |
| `CEN_AVG_DEUDA_12M` | Promedio de la deuda total en los últimos 12 meses | AVG(DEUDA) sobre t-11..t | nunca | + | modelo | 0,09 | NO | NO | NO |
| `CEN_MAX_DEUDA_3M` | Máximo de la deuda total en los últimos 3 meses | MAX(DEUDA) sobre t-2..t | nunca | + | modelo | 0,11 | NO | NO | NO |
| `CEN_MAX_DEUDA_6M` | Máximo de la deuda total en los últimos 6 meses | MAX(DEUDA) sobre t-5..t | nunca | + | modelo | 0,09 | NO | NO | NO |
| `CEN_MAX_DEUDA_12M` | Máximo de la deuda total en los últimos 12 meses | MAX(DEUDA) sobre t-11..t | nunca | + | modelo | 0,08 | NO | NO | NO |
| `CEN_AVG_DEUDA_IRR_3M` | Promedio de la deuda en situación 2 o peor en los últimos 3 meses | AVG(DEUDA_IRR) sobre t-2..t | nunca | + | modelo | 1,44 | NO | SI | NO |
| `CEN_AVG_DEUDA_IRR_6M` | Promedio de la deuda en situación 2 o peor en los últimos 6 meses | AVG(DEUDA_IRR) sobre t-5..t | nunca | + | modelo | 1,35 | NO | SI | NO |
| `CEN_AVG_DEUDA_IRR_12M` | Promedio de la deuda en situación 2 o peor en los últimos 12 meses | AVG(DEUDA_IRR) sobre t-11..t | nunca | + | modelo | 1,25 | NO | SI | NO |
| `CEN_MAX_DEUDA_IRR_3M` | Máximo de la deuda en situación 2 o peor en los últimos 3 meses | MAX(DEUDA_IRR) sobre t-2..t | nunca | + | modelo | 1,44 | NO | NO | NO |
| `CEN_MAX_DEUDA_IRR_6M` | Máximo de la deuda en situación 2 o peor en los últimos 6 meses | MAX(DEUDA_IRR) sobre t-5..t | nunca | + | modelo | 1,35 | NO | NO | NO |
| `CEN_MAX_DEUDA_IRR_12M` | Máximo de la deuda en situación 2 o peor en los últimos 12 meses | MAX(DEUDA_IRR) sobre t-11..t | nunca | + | modelo | 1,25 | NO | NO | NO |
| `CEN_PCT_DEUDA_IRR_1M` | Proporción de la deuda actual en situación 2 o peor | DEUDA_IRR_t / DEUDA_t | sin deuda en el mes | + | modelo | 1,53 | NO | NO | NO |
| `CEN_AVG_PCT_DEUDA_IRR_3M` | Promedio de la proporción de deuda irregular en los últimos 3 meses | AVG(PCT_IRR) sobre t-2..t, ignorando meses sin deuda | sin deuda en toda la ventana | + | modelo | 1,44 | NO | NO | NO |
| `CEN_AVG_PCT_DEUDA_IRR_6M` | Promedio de la proporción de deuda irregular en los últimos 6 meses | AVG(PCT_IRR) sobre t-5..t, ignorando meses sin deuda | sin deuda en toda la ventana | + | modelo | 1,35 | NO | NO | NO |
| `CEN_AVG_PCT_DEUDA_IRR_12M` | Promedio de la proporción de deuda irregular en los últimos 12 meses | AVG(PCT_IRR) sobre t-11..t, ignorando meses sin deuda | sin deuda en toda la ventana | + | modelo | 1,26 | NO | NO | NO |

<a id="b02_calidad"></a>
## B02_CALIDAD — Qué tan mala es y fue su situación

| Variable | Descripción | Definición | Nulos | Sentido | Uso | IV | Rec. | S1 | S2 |
|---|---|---|---|---|---|---|---|---|---|
| `CEN_MAX_SIT_1M` | Peor situación en los últimos 1 meses | MAX(SIT) sobre t-0..t | nunca | + | modelo | 1,53 | NO | NO | NO |
| `CEN_MAX_SIT_3M` | Peor situación en los últimos 3 meses | MAX(SIT) sobre t-2..t | nunca | + | modelo | 1,44 | NO | NO | NO |
| `CEN_MAX_SIT_6M` | Peor situación en los últimos 6 meses | MAX(SIT) sobre t-5..t | nunca | + | modelo | 1,40 | SI | NO | NO |
| `CEN_MAX_SIT_12M` | Peor situación en los últimos 12 meses | MAX(SIT) sobre t-11..t | nunca | + | modelo | 1,29 | NO | NO | NO |
| `CEN_AVG_SIT_POND_1M` | Situación ponderada por monto, promedio de los últimos 1 meses | AVG(SIT_POND) sobre t-0..t, ignorando meses ausente | ausente en toda la ventana | + | modelo | 1,53 | NO | NO | NO |
| `CEN_AVG_SIT_POND_3M` | Situación ponderada por monto, promedio de los últimos 3 meses | AVG(SIT_POND) sobre t-2..t, ignorando meses ausente | ausente en toda la ventana | + | modelo | 1,44 | NO | NO | SI |
| `CEN_AVG_SIT_POND_6M` | Situación ponderada por monto, promedio de los últimos 6 meses | AVG(SIT_POND) sobre t-5..t, ignorando meses ausente | ausente en toda la ventana | + | modelo | 1,35 | NO | NO | NO |
| `CEN_AVG_SIT_POND_12M` | Situación ponderada por monto, promedio de los últimos 12 meses | AVG(SIT_POND) sobre t-11..t, ignorando meses ausente | ausente en toda la ventana | + | modelo | 1,25 | NO | NO | NO |
| `CEN_CNT_MESES_SIT2P_3M` | Meses con situación 2 o peor en los últimos 3 meses | COUNT(SIT >= 2) sobre t-2..t | nunca | + | modelo | 1,44 | NO | NO | NO |
| `CEN_CNT_MESES_SIT2P_6M` | Meses con situación 2 o peor en los últimos 6 meses | COUNT(SIT >= 2) sobre t-5..t | nunca | + | modelo | 1,36 | NO | NO | NO |
| `CEN_CNT_MESES_SIT2P_12M` | Meses con situación 2 o peor en los últimos 12 meses | COUNT(SIT >= 2) sobre t-11..t | nunca | + | modelo | 1,24 | NO | NO | SI |
| `CEN_CNT_MESES_SIT3P_6M` | Meses con situación 3 o peor en los últimos 6 meses | COUNT(SIT >= 3) sobre t-5..t | nunca | + | modelo | 0,07 | NO | NO | NO |
| `CEN_CNT_MESES_SIT3P_12M` | Meses con situación 3 o peor en los últimos 12 meses | COUNT(SIT >= 3) sobre t-11..t | nunca | + | modelo | 0,09 | NO | NO | NO |
| `CEN_MOD_SIT_12M` | Situación más frecuente en los últimos 12 meses (incluye 0 = ausente) | MODE(SIT) sobre t-11..t; desempate por la peor | nunca | + | modelo | 0,06 | NO | NO | SI |
| `CEN_RCM_SIT2P_12M` | Meses desde la última vez en situación 2 o peor | t - último mes con SIT >= 2 en t-11..t | nunca tuvo situación 2+ en la ventana | - | modelo | 1,74 | SI | NO | NO |
| `CEN_RCM_SIT3P_12M` | Meses desde la última vez en situación 3 o peor | t - último mes con SIT >= 3 en t-11..t | nunca tuvo situación 3+ en la ventana | - | modelo | 0,10 | NO | NO | NO |
| `CEN_RCM_PEOR_SIT_12M` | Meses desde el peor momento (máxima situación) de los últimos 12 meses | t - último mes con SIT = MAX(SIT) en t-11..t | nunca | + | modelo | 0,13 | NO | NO | NO |
| `CEN_FLG_PEOR_MOMENTO_1M` | El mes de observación es el peor de los últimos 12 meses y la situación es 2 o peor | SIT_t = MAX(SIT en t-11..t) AND SIT_t >= 2 | nunca | + | modelo | 1,36 | NO | NO | NO |
| `CEN_CNT_ENT_SIT2P_1M` | Entidades en las que tiene situación 2 o peor en el mes | ENT_IRR_t | nunca | + | modelo | 1,53 | NO | NO | SI |
| `CEN_CNT_ENT_SIT3P_12M` | Entidades distintas en las que tuvo situación 3 o peor en los últimos 12 meses | COUNT DISTINCT entidad con situación >= 3 en t-11..t | nunca | + | modelo | 0,09 | NO | NO | NO |
| `CEN_FLG_PROC_JUDICIAL_12M` | Alguna deuda informada en proceso judicial o revisión (Ley 25.326) en los últimos 12 meses | MAX(flag IN (1, 2)) en t-11..t | nunca | + | modelo | 0,00 | NO | NO | NO |

Notas:
- `CEN_CNT_MESES_SIT3P_6M`: La población del target excluye SIT_t >= 3, pero puede haberla tenido antes.
- `CEN_CNT_MESES_SIT3P_12M`: La población del target excluye SIT_t >= 3, pero puede haberla tenido antes.
- `CEN_RCM_PEOR_SIT_12M`: Sentido observado + (hipótesis original -, informe de poder v2). Quien siempre estuvo en situación 1 tiene su peor momento en t (recencia 0), así que valores bajos marcan a los limpios.
- `CEN_FLG_PROC_JUDICIAL_12M`: 460 filas en 24 meses; se mantiene por pedido del estudio pero se espera poder casi nulo.

<a id="b03_transiciones"></a>
## B03_TRANSICIONES — Cómo se mueve entre situaciones (rolls, curas, rachas)

| Variable | Descripción | Definición | Nulos | Sentido | Uso | IV | Rec. | S1 | S2 |
|---|---|---|---|---|---|---|---|---|---|
| `CEN_CNT_EMPEORA_6M` | Meses en que empeoró la situación respecto del mes anterior, últimos 6 meses | COUNT(SIT_m > SIT_{m-1} AND SIT_{m-1} >= 1) para m en t-4..t | nunca | + | modelo | 1,34 | NO | NO | NO |
| `CEN_CNT_EMPEORA_12M` | Meses en que empeoró la situación respecto del mes anterior, últimos 12 meses | COUNT(SIT_m > SIT_{m-1} AND SIT_{m-1} >= 1) para m en t-10..t | nunca | + | modelo | 1,24 | NO | NO | NO |
| `CEN_CNT_MEJORA_6M` | Meses en que mejoró la situación respecto del mes anterior, últimos 6 meses | COUNT(SIT_m < SIT_{m-1} AND SIT_m >= 1) para m en t-4..t | nunca | + | modelo | 0,21 | SI | NO | NO |
| `CEN_CNT_MEJORA_12M` | Meses en que mejoró la situación respecto del mes anterior, últimos 12 meses | COUNT(SIT_m < SIT_{m-1} AND SIT_m >= 1) para m en t-10..t | nunca | + | modelo | 0,25 | SI | NO | NO |
| `CEN_RAT_CURA_12M` | Tasa de cura individual, salidas de situación 2+ a situación 1 sobre meses en situación 2+ | COUNT(SIT_{m-1} >= 2 AND SIT_m = 1) / COUNT(SIT_{m-1} >= 2) en t-11..t | no estuvo en situación 2+ en la ventana | - | modelo | 0,88 | SI | SI | NO |
| `CEN_RAT_ROLL_2A3_12M` | Tasa de roll individual de situación 2 a 3 o peor | COUNT(SIT_{m-1} = 2 AND SIT_m >= 3) / COUNT(SIT_{m-1} = 2) en t-11..t | no estuvo en situación 2 en la ventana | - | modelo | 0,70 | SI | SI | NO |
| `CEN_MAX_RACHA_SIT2P_12M` | Racha más larga de meses consecutivos en situación 2 o peor | máxima longitud de corrida SIT >= 2 en t-11..t | nunca | + | modelo | 1,24 | NO | NO | NO |
| `CEN_CNT_RACHA_SIT2P_1M` | Meses consecutivos en situación 2 o peor hasta el mes de observación | longitud de la corrida SIT >= 2 que termina en t (0 si SIT_t < 2) | nunca | + | modelo | 1,53 | NO | NO | SI |
| `CEN_CNT_RACHA_SIT1_1M` | Meses consecutivos en situación 1 hasta el mes de observación (tope 12) | longitud de la corrida SIT = 1 que termina en t, contando ausencias intermedias como neutras | nunca | - | modelo | 1,36 | NO | SI | NO |
| `CEN_MAX_SALTO_SIT_12M` | Mayor empeoramiento de situación entre dos meses consecutivos | MAX(SIT_m - SIT_{m-1}) con SIT_{m-1} >= 1, en t-11..t (0 si nunca empeoró) | nunca | + | modelo | 1,25 | NO | NO | NO |
| `CEN_FLG_ENTRADA_SIT4P_12M` | Alguna relación nueva con una entidad que aparece directamente en situación 4 o peor | existe (entidad, m) en t-11..t con situación >= 4 y sin presencia de esa entidad en m-1 | nunca | + | modelo | 0,01 | NO | NO | NO |
| `CEN_CNT_ENT_EMPEORA_3M` | Entidades en las que la situación actual es peor que hace 3 meses | COUNT entidades con situación_t > situación_{t-2} (presentes en ambos meses) | nunca | + | modelo | 1,34 | NO | NO | SI |

Notas:
- `CEN_CNT_EMPEORA_6M`: No cuenta las altas (0 -> k).
- `CEN_CNT_EMPEORA_12M`: No cuenta las altas (0 -> k).
- `CEN_CNT_MEJORA_6M`: Sentido observado + (hipótesis original -, informe de poder v2). Para mejorar hay que haber estado peor: el conteo funciona como marca de historia irregular.
- `CEN_CNT_MEJORA_12M`: Sentido observado + (hipótesis original -, informe de poder v2). Para mejorar hay que haber estado peor: el conteo funciona como marca de historia irregular.
- `CEN_RAT_ROLL_2A3_12M`: Roll de 2 a 3 del sistema = 34% mensual (estudio, sección 4). Sentido observado - (hipótesis original +, informe de poder v2): la población exige SIT <= 2 en t, así que quien ya pasó a 3 y volvió demostró cura, mientras que una tasa 0 incluye a los que están en situación 2 por primera vez.
- `CEN_CNT_RACHA_SIT1_1M`: Versión sin tope en CEN_CNT_MESES_SIT1_ININT_1M (bloque persona, desde 1DSF).
- `CEN_FLG_ENTRADA_SIT4P_12M`: Proxy de cartera cedida o comprada (estudio, hallazgo 6; fideicomisos de recupero).

<a id="b04_velocidad"></a>
## B04_VELOCIDAD — A qué ritmo cambian deuda, irregularidad y relaciones

| Variable | Descripción | Definición | Nulos | Sentido | Uso | IV | Rec. | S1 | S2 |
|---|---|---|---|---|---|---|---|---|---|
| `CEN_VAR_DEUDA_1M` | Variación relativa de la deuda total respecto del mes anterior | (DEUDA_t - DEUDA_{t-1}) / DEUDA_{t-1} | sin deuda el mes anterior | + | modelo | 0,35 | SI | SI | NO |
| `CEN_AVG_VAR_DEUDA_6M` | Promedio de las variaciones mensuales de la deuda en los últimos 6 meses | AVG(VAR(DEUDA)) sobre t-4..t, ignorando meses con deuda previa 0 | sin variaciones calculables | + | modelo | 0,10 | SI | NO | NO |
| `CEN_AVG_VAR_DEUDA_12M` | Promedio de las variaciones mensuales de la deuda en los últimos 12 meses | AVG(VAR(DEUDA)) sobre t-10..t, ignorando meses con deuda previa 0 | sin variaciones calculables | + | modelo | 0,05 | NO | NO | SI |
| `CEN_TEN_DEUDA_3M` | Deuda total (todas las entidades): valor actual sobre el de hace 3 meses | DEUDA_t / DEUDA_(t-2) | valor 0 al inicio de la ventana | + | modelo | 0,25 | SI | SI | NO |
| `CEN_TEN_DEUDA_6M` | Deuda total (todas las entidades): valor actual sobre el de hace 6 meses | DEUDA_t / DEUDA_(t-5) | valor 0 al inicio de la ventana | + | modelo | 0,17 | NO | NO | NO |
| `CEN_TEN_DEUDA_12M` | Deuda total (todas las entidades): valor actual sobre el de hace 12 meses | DEUDA_t / DEUDA_(t-11) | valor 0 al inicio de la ventana | + | modelo | 0,23 | NO | NO | NO |
| `CEN_TEN_DEUDA_IRR_3M` | Deuda en situación 2 o peor: valor actual sobre el de hace 3 meses | DEUDA_IRR_t / DEUDA_IRR_(t-2) | valor 0 al inicio de la ventana | + | modelo | 0,23 | NO | SI | SI |
| `CEN_TEN_DEUDA_IRR_6M` | Deuda en situación 2 o peor: valor actual sobre el de hace 6 meses | DEUDA_IRR_t / DEUDA_IRR_(t-5) | valor 0 al inicio de la ventana | + | modelo | 0,15 | NO | NO | NO |
| `CEN_TEN_DEUDA_IRR_12M` | Deuda en situación 2 o peor: valor actual sobre el de hace 12 meses | DEUDA_IRR_t / DEUDA_IRR_(t-11) | valor 0 al inicio de la ventana | + | modelo | 0,09 | NO | NO | NO |
| `CEN_TEN_ENT_3M` | Cantidad de entidades: valor actual sobre el de hace 3 meses | ENT_t / ENT_(t-2) | valor 0 al inicio de la ventana | + | modelo | 0,07 | NO | NO | NO |
| `CEN_TEN_ENT_6M` | Cantidad de entidades: valor actual sobre el de hace 6 meses | ENT_t / ENT_(t-5) | valor 0 al inicio de la ventana | + | modelo | 0,21 | SI | NO | SI |
| `CEN_TEN_ENT_12M` | Cantidad de entidades: valor actual sobre el de hace 12 meses | ENT_t / ENT_(t-11) | valor 0 al inicio de la ventana | + | modelo | 0,37 | NO | NO | SI |
| `CEN_FMX_DEUDA_3M` | Deuda total (todas las entidades): peso del mes actual en el total de los últimos 3 meses | DEUDA_t / SUM(DEUDA) sobre t-2..t | suma 0 en la ventana | + | modelo | 0,27 | NO | NO | SI |
| `CEN_FMX_DEUDA_6M` | Deuda total (todas las entidades): peso del mes actual en el total de los últimos 6 meses | DEUDA_t / SUM(DEUDA) sobre t-5..t | suma 0 en la ventana | + | modelo | 0,20 | NO | NO | NO |
| `CEN_FMX_DEUDA_12M` | Deuda total (todas las entidades): peso del mes actual en el total de los últimos 12 meses | DEUDA_t / SUM(DEUDA) sobre t-11..t | suma 0 en la ventana | + | modelo | 0,25 | NO | SI | NO |
| `CEN_FMX_ENT_3M` | Cantidad de entidades: peso del mes actual en el total de los últimos 3 meses | ENT_t / SUM(ENT) sobre t-2..t | suma 0 en la ventana | + | modelo | 0,06 | NO | NO | NO |
| `CEN_FMX_ENT_6M` | Cantidad de entidades: peso del mes actual en el total de los últimos 6 meses | ENT_t / SUM(ENT) sobre t-5..t | suma 0 en la ventana | + | modelo | 0,19 | NO | NO | NO |
| `CEN_FMX_ENT_12M` | Cantidad de entidades: peso del mes actual en el total de los últimos 12 meses | ENT_t / SUM(ENT) sobre t-11..t | suma 0 en la ventana | + | modelo | 0,31 | SI | SI | SI |
| `CEN_DIF_SIT_3M` | Cambio de la peor situación respecto de hace 3 meses | SIT_t - SIT_(t-2) | nunca | + | modelo | 0,75 | NO | NO | NO |
| `CEN_DIF_SIT_6M` | Cambio de la peor situación respecto de hace 6 meses | SIT_t - SIT_(t-5) | nunca | + | modelo | 0,82 | NO | NO | NO |
| `CEN_DIF_SIT_12M` | Cambio de la peor situación respecto de hace 12 meses | SIT_t - SIT_(t-11) | nunca | + | modelo | 0,88 | SI | NO | NO |
| `CEN_CV_DEUDA_6M` | Coeficiente de variación de la deuda en los últimos 6 meses | STD(DEUDA) / AVG(DEUDA) sobre t-5..t | promedio 0 | ? | modelo | 0,08 | NO | NO | NO |
| `CEN_CV_DEUDA_12M` | Coeficiente de variación de la deuda en los últimos 12 meses | STD(DEUDA) / AVG(DEUDA) sobre t-11..t | promedio 0 | ? | modelo | 0,06 | NO | NO | SI |
| `CEN_PND_DEUDA_6M` | Pendiente relativa de la deuda en los últimos 6 meses (crecimiento mensual promedio) | REGR_SLOPE(DEUDA, m) / AVG(DEUDA) sobre t-5..t | promedio 0 | + | modelo | 0,16 | NO | NO | NO |
| `CEN_PND_DEUDA_12M` | Pendiente relativa de la deuda en los últimos 12 meses (crecimiento mensual promedio) | REGR_SLOPE(DEUDA, m) / AVG(DEUDA) sobre t-11..t | promedio 0 | + | modelo | 0,25 | NO | NO | NO |

Notas:
- `CEN_CV_DEUDA_6M`: Hipótesis original +. En el informe de poder v2 el sentido observado es - a 6 meses y + a 12 meses, con IV < 0,1: relación no monótona.
- `CEN_CV_DEUDA_12M`: Hipótesis original +. En el informe de poder v2 el sentido observado es - a 6 meses y + a 12 meses, con IV < 0,1: relación no monótona.

<a id="b05_momentum"></a>
## B05_MOMENTUM — RSI, fuerza de la tendencia reciente

| Variable | Descripción | Definición | Nulos | Sentido | Uso | IV | Rec. | S1 | S2 |
|---|---|---|---|---|---|---|---|---|---|
| `CEN_RSI_DEUDA_3M` | Deuda total (todas las entidades): RSI de los últimos 3 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de DEUDA en t-2..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo | 0,19 | NO | NO | NO |
| `CEN_RSI_DEUDA_6M` | Deuda total (todas las entidades): RSI de los últimos 6 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de DEUDA en t-5..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo | 0,32 | SI | SI | SI |
| `CEN_RSI_DEUDA_12M` | Deuda total (todas las entidades): RSI de los últimos 12 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de DEUDA en t-11..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo | 0,53 | SI | SI | SI |
| `CEN_RSI_DEUDA_IRR_3M` | Deuda en situación 2 o peor: RSI de los últimos 3 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de DEUDA_IRR en t-2..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo | 1,64 | SI | SI | SI |
| `CEN_RSI_DEUDA_IRR_6M` | Deuda en situación 2 o peor: RSI de los últimos 6 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de DEUDA_IRR en t-5..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo | 1,70 | NO | SI | NO |
| `CEN_RSI_DEUDA_IRR_12M` | Deuda en situación 2 o peor: RSI de los últimos 12 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de DEUDA_IRR en t-11..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo | 1,72 | NO | SI | NO |
| `CEN_RSI_ENT_3M` | Cantidad de entidades: RSI de los últimos 3 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de ENT en t-2..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo | 0,06 | NO | SI | NO |
| `CEN_RSI_ENT_6M` | Cantidad de entidades: RSI de los últimos 6 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de ENT en t-5..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo | 0,26 | NO | SI | NO |
| `CEN_RSI_ENT_12M` | Cantidad de entidades: RSI de los últimos 12 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de ENT en t-11..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo | 0,43 | SI | SI | NO |
| `CEN_RSI_SIT_3M` | Peor situación entre entidades (0 = ausente): RSI de los últimos 3 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de SIT en t-2..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo | 0,89 | NO | NO | NO |
| `CEN_RSI_SIT_6M` | Peor situación entre entidades (0 = ausente): RSI de los últimos 6 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de SIT en t-5..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo | 0,92 | SI | NO | NO |
| `CEN_RSI_SIT_12M` | Peor situación entre entidades (0 = ausente): RSI de los últimos 12 meses | 100 - 100 / (1 + SUM(subas) / SUM(bajas)) de las diferencias mensuales de SIT en t-11..t; 100 si sólo hay subas | sin variaciones en la ventana (serie constante) | + | modelo | 0,82 | NO | NO | NO |

Notas:
- `CEN_RSI_DEUDA_3M`: Sobre la grilla densa (en el CRC se calculaba con huecos).
- `CEN_RSI_DEUDA_6M`: Sobre la grilla densa (en el CRC se calculaba con huecos).
- `CEN_RSI_DEUDA_12M`: Sobre la grilla densa (en el CRC se calculaba con huecos).
- `CEN_RSI_DEUDA_IRR_3M`: Sobre la grilla densa (en el CRC se calculaba con huecos).
- `CEN_RSI_DEUDA_IRR_6M`: Sobre la grilla densa (en el CRC se calculaba con huecos).
- `CEN_RSI_DEUDA_IRR_12M`: Sobre la grilla densa (en el CRC se calculaba con huecos).
- `CEN_RSI_ENT_3M`: Sobre la grilla densa (en el CRC se calculaba con huecos).
- `CEN_RSI_ENT_6M`: Sobre la grilla densa (en el CRC se calculaba con huecos).
- `CEN_RSI_ENT_12M`: Sobre la grilla densa (en el CRC se calculaba con huecos).
- `CEN_RSI_SIT_3M`: Sobre la grilla densa (en el CRC se calculaba con huecos).
- `CEN_RSI_SIT_6M`: Sobre la grilla densa (en el CRC se calculaba con huecos).
- `CEN_RSI_SIT_12M`: Sobre la grilla densa (en el CRC se calculaba con huecos).

<a id="b06_relacion_sistema"></a>
## B06_RELACION_SISTEMA — Con cuántas y cuáles entidades opera, altas, bajas, principalidad, concentración

| Variable | Descripción | Definición | Nulos | Sentido | Uso | IV | Rec. | S1 | S2 |
|---|---|---|---|---|---|---|---|---|---|
| `CEN_CNT_ENT_1M` | Entidades con las que tiene deuda en el mes | ENT_t | nunca | + | modelo | 0,34 | NO | NO | NO |
| `CEN_AVG_ENT_6M` | Promedio de la cantidad de entidades en los últimos 6 meses | AVG(ENT) sobre t-5..t | nunca | + | modelo | 0,43 | NO | NO | NO |
| `CEN_AVG_ENT_12M` | Promedio de la cantidad de entidades en los últimos 12 meses | AVG(ENT) sobre t-11..t | nunca | + | modelo | 0,45 | SI | SI | NO |
| `CEN_MAX_ENT_6M` | Máximo de la cantidad de entidades en los últimos 6 meses | MAX(ENT) sobre t-5..t | nunca | + | modelo | 0,32 | NO | NO | NO |
| `CEN_MAX_ENT_12M` | Máximo de la cantidad de entidades en los últimos 12 meses | MAX(ENT) sobre t-11..t | nunca | + | modelo | 0,31 | NO | NO | NO |
| `CEN_CNT_ENT_DISTINTAS_12M` | Entidades distintas con las que tuvo deuda en los últimos 12 meses | COUNT DISTINCT entidad en t-11..t | nunca | + | modelo | 0,33 | NO | NO | NO |
| `CEN_CNT_ALTAS_3M` | Relaciones nuevas con entidades en los últimos 3 meses | COUNT (entidad, m) en t-2..t con deuda en m y sin deuda en esa entidad en m-1 | nunca | + | modelo | 0,12 | NO | NO | NO |
| `CEN_CNT_ALTAS_6M` | Relaciones nuevas con entidades en los últimos 6 meses | COUNT (entidad, m) en t-5..t con deuda en m y sin deuda en esa entidad en m-1 | nunca | + | modelo | 0,28 | NO | NO | NO |
| `CEN_CNT_ALTAS_12M` | Relaciones nuevas con entidades en los últimos 12 meses | COUNT (entidad, m) en t-11..t con deuda en m y sin deuda en esa entidad en m-1 | nunca | + | modelo | 0,38 | SI | SI | NO |
| `CEN_CNT_BAJAS_3M` | Relaciones con entidades que se cerraron en los últimos 3 meses | COUNT (entidad, m) en t-2..t sin deuda en m y con deuda en esa entidad en m-1 | nunca | + | modelo | 0,02 | NO | NO | NO |
| `CEN_CNT_BAJAS_6M` | Relaciones con entidades que se cerraron en los últimos 6 meses | COUNT (entidad, m) en t-5..t sin deuda en m y con deuda en esa entidad en m-1 | nunca | + | modelo | 0,04 | NO | NO | NO |
| `CEN_CNT_BAJAS_12M` | Relaciones con entidades que se cerraron en los últimos 12 meses | COUNT (entidad, m) en t-11..t sin deuda en m y con deuda en esa entidad en m-1 | nunca | + | modelo | 0,04 | NO | NO | NO |
| `CEN_RCM_ULTIMA_ALTA_12M` | Meses desde la última relación nueva | t - último mes con un alta en t-11..t | sin altas en la ventana | - | modelo | 0,35 | SI | SI | SI |
| `CEN_RAT_PRINCIPALIDAD_1M` | Peso de la entidad principal en la deuda total | deuda en ENT_PRINC_t / DEUDA_t | sin deuda en el mes | - | modelo | 0,22 | NO | NO | NO |
| `CEN_CNT_CAMBIO_PRINC_6M` | Veces que cambió la entidad principal en los últimos 6 meses | COUNT(ENT_PRINC_m <> ENT_PRINC_{m-1}) con deuda en ambos meses, m en t-4..t | nunca | + | modelo | 0,04 | NO | NO | NO |
| `CEN_CNT_CAMBIO_PRINC_12M` | Veces que cambió la entidad principal en los últimos 12 meses | COUNT(ENT_PRINC_m <> ENT_PRINC_{m-1}) con deuda en ambos meses, m en t-10..t | nunca | + | modelo | 0,07 | NO | NO | NO |
| `CEN_IDX_HHI_1M` | Concentración de la deuda entre entidades (1 = una sola entidad) | HHI_t = SUM((deuda_entidad / DEUDA_t)^2) | sin deuda en el mes | - | modelo | 0,24 | SI | SI | NO |
| `CEN_CNT_MESES_PRESENTE_12M` | Meses con deuda informada en los últimos 12 meses | COUNT(SIT >= 1) en t-11..t | nunca | - | modelo | 0,05 | NO | NO | NO |
| `CEN_CNT_HUECOS_12M` | Meses ausente entre la primera y la última aparición de los últimos 12 meses | (último mes presente - primer mes presente + 1) - CNT_MESES_PRESENTE en t-11..t | nunca | - | modelo | 0,00 | NO | NO | NO |
| `CEN_RCM_PRIMERA_APARICION_12M` | Antigüedad en la central dentro de la ventana, meses desde la primera aparición | t - primer mes con SIT >= 1 en t-11..t | nunca (la población está presente en t) | - | modelo | 0,07 | NO | NO | SI |
| `CEN_FLG_REACTIVACION_12M` | Volvió a la central después de 3 o más meses ausente | existe m en t-11..t con SIT_m >= 1 y SIT = 0 en m-3..m-1 y presencia antes de m-3 | nunca | - | modelo | 0,00 | NO | NO | NO |

Notas:
- `CEN_CNT_ALTAS_3M`: Requiere un mes previo a la ventana; con equivalencias aplicadas.
- `CEN_CNT_ALTAS_6M`: Requiere un mes previo a la ventana; con equivalencias aplicadas.
- `CEN_CNT_ALTAS_12M`: Requiere un mes previo a la ventana; con equivalencias aplicadas.
- `CEN_CNT_BAJAS_3M`: Sentido observado + en las tres ventanas (hipótesis original -, informe de poder v2): cerrar relaciones acompaña a refinanciaciones, pases a pérdida o recortes de las entidades.
- `CEN_CNT_BAJAS_6M`: Sentido observado + en las tres ventanas (hipótesis original -, informe de poder v2): cerrar relaciones acompaña a refinanciaciones, pases a pérdida o recortes de las entidades.
- `CEN_CNT_BAJAS_12M`: Sentido observado + en las tres ventanas (hipótesis original -, informe de poder v2): cerrar relaciones acompaña a refinanciaciones, pases a pérdida o recortes de las entidades.
- `CEN_CNT_HUECOS_12M`: El 21% de los CUITs tiene huecos (estudio, hallazgo 4). Sentido observado - (hipótesis original +, informe de poder v2) con IV ≈ 0: el sentido es poco confiable.
- `CEN_RCM_PRIMERA_APARICION_12M`: Truncada a 12 meses por la historia disponible; complementa CEN_CNT_MESES_SIT1_ININT_1M.
- `CEN_FLG_REACTIVACION_12M`: Sentido observado - (hipótesis original +, informe de poder v2) con IV ≈ 0: el sentido es poco confiable.

<a id="b07_mix_entidades"></a>
## B07_MIX_ENTIDADES — Con qué tipo de entidades se endeuda

| Variable | Descripción | Definición | Nulos | Sentido | Uso | IV | Rec. | S1 | S2 |
|---|---|---|---|---|---|---|---|---|---|
| `CEN_PCT_DEUDA_BANCO_1M` | Proporción de la deuda con bancos | deuda con tipo_agrupado BANCO / DEUDA_t | sin deuda en el mes | según tipo: bancos -, PNFC y fideicomisos + | modelo | 0,34 | SI | SI | NO |
| `CEN_PCT_DEUDA_CIA_FIN_1M` | Proporción de la deuda con compañías financieras | deuda con tipo_agrupado CIA_FIN / DEUDA_t | sin deuda en el mes | según tipo: bancos -, PNFC y fideicomisos + | modelo | 0,07 | NO | NO | NO |
| `CEN_PCT_DEUDA_TARJETA_NB_1M` | Proporción de la deuda con emisoras no bancarias de tarjetas | deuda con tipo_agrupado TARJETA_NB / DEUDA_t | sin deuda en el mes | según tipo: bancos -, PNFC y fideicomisos + | modelo | 0,10 | NO | NO | NO |
| `CEN_PCT_DEUDA_PNFC_1M` | Proporción de la deuda con proveedores no financieros de crédito | deuda con tipo_agrupado PNFC / DEUDA_t | sin deuda en el mes | según tipo: bancos -, PNFC y fideicomisos + | modelo | 0,41 | NO | NO | NO |
| `CEN_PCT_DEUDA_FIDEICOMISO_1M` | Proporción de la deuda con fideicomisos financieros | deuda con tipo_agrupado FIDEICOMISO / DEUDA_t | sin deuda en el mes | según tipo: bancos -, PNFC y fideicomisos + | modelo | 0,00 | NO | NO | NO |
| `CEN_CNT_ENT_BANCO_1M` | Cantidad de bancos con las que tiene deuda | COUNT DISTINCT entidad con tipo_agrupado BANCO en t | nunca | según tipo | modelo | 0,11 | SI | SI | NO |
| `CEN_CNT_ENT_TARJETA_NB_1M` | Cantidad de emisoras no bancarias de tarjetas con las que tiene deuda | COUNT DISTINCT entidad con tipo_agrupado TARJETA_NB en t | nunca | según tipo | modelo | 0,07 | NO | NO | NO |
| `CEN_CNT_ENT_PNFC_1M` | Cantidad de proveedores no financieros de crédito con las que tiene deuda | COUNT DISTINCT entidad con tipo_agrupado PNFC en t | nunca | según tipo | modelo | 0,49 | SI | SI | NO |
| `CEN_CNT_TIPOS_ENT_1M` | Cantidad de tipos de entidad distintos con los que tiene deuda | COUNT DISTINCT tipo_agrupado en t | nunca | + | modelo | 0,30 | SI | SI | NO |
| `CEN_IDX_ENTROPIA_TIPO_1M` | Entropía de Shannon de la deuda por tipo de entidad (promedio de 1 meses) | AVG(-SUM(p_tipo * ln p_tipo)) sobre t-0..t, p_tipo = participación de la deuda por tipo_agrupado | sin deuda en toda la ventana | + | modelo | 0,27 | NO | NO | NO |
| `CEN_IDX_ENTROPIA_TIPO_6M` | Entropía de Shannon de la deuda por tipo de entidad (promedio de 6 meses) | AVG(-SUM(p_tipo * ln p_tipo)) sobre t-5..t, p_tipo = participación de la deuda por tipo_agrupado | sin deuda en toda la ventana | + | modelo | 0,26 | NO | NO | NO |
| `CEN_FLG_SOLO_NO_BANCARIA_1M` | Toda su deuda es con entidades no bancarias (ni bancos ni compañías financieras) | DEUDA_t > 0 AND deuda con BANCO y CIA_FIN = 0 | nunca | + | modelo | 0,02 | NO | NO | NO |
| `CEN_MAX_SIT_BANCARIA_12M` | Peor situación con entidades del grupo BANCARIA en los últimos 12 meses | MAX(situación) con entidades BANCO o CIA_FIN (BANCARIA) / resto (NO_BANCARIA) en t-11..t; 0 si no tuvo | nunca | + | modelo | 0,67 | SI | SI | NO |
| `CEN_MAX_SIT_NO_BANCARIA_12M` | Peor situación con entidades del grupo NO_BANCARIA en los últimos 12 meses | MAX(situación) con entidades BANCO o CIA_FIN (BANCARIA) / resto (NO_BANCARIA) en t-11..t; 0 si no tuvo | nunca | + | modelo | 0,89 | SI | SI | NO |
| `CEN_TEN_DEUDA_PNFC_6M` | Deuda con proveedores no financieros, actual sobre la de hace 6 meses | deuda PNFC_t / deuda PNFC_(t-5) | sin deuda PNFC hace 6 meses | + | modelo | 0,27 | SI | SI | NO |
| `CEN_FLG_FIDEICOMISO_RECUPERO_12M` | Tuvo deuda con un fideicomiso de recupero de cartera en los últimos 12 meses | deuda en t-11..t con una entidad marcada recupero = 1 en referencias/entidades.csv | nunca | + | modelo | 0,00 | NO | NO | NO |

Notas:
- `CEN_FLG_FIDEICOMISO_RECUPERO_12M`: Marca por nombre (Recupero, Asset Recovery, Gestión de Activos); los fideicomisos sin nombre en el maestro quedan sin marcar.

<a id="b08_foto_mensual"></a>
## B08_FOTO_MENSUAL — Campos que sólo trae el mensual (atraso, refinanciación, garante)

| Variable | Descripción | Definición | Nulos | Sentido | Uso | IV | Rec. | S1 | S2 |
|---|---|---|---|---|---|---|---|---|---|
| `CEN_MAX_DIAS_ATRASO_1M` | Máximo de días de atraso informado entre entidades | MAX(dias_atraso) en t | ninguna entidad informa días de atraso para el CUIT | + | futuro |  |  |  |  |
| `CEN_CNT_ENT_REFINANCIACION_1M` | Entidades que informan REFINANCIACION = 1 | COUNT entidades con el campo REFINANCIACION = 1 en t | nunca (9 = no aplica cuenta como 0) | + | futuro |  |  |  |  |
| `CEN_CNT_ENT_RECATEG_OBLIG_1M` | Entidades que informan RECATEG_OBLIG = 1 | COUNT entidades con el campo RECATEG_OBLIG = 1 en t | nunca (9 = no aplica cuenta como 0) | + | futuro |  |  |  |  |
| `CEN_CNT_ENT_SIT_JURIDICA_1M` | Entidades que informan SIT_JURIDICA = 1 | COUNT entidades con el campo SIT_JURIDICA = 1 en t | nunca (9 = no aplica cuenta como 0) | + | futuro |  |  |  |  |
| `CEN_CNT_ENT_IRRECUP_DISP_TECNICA_1M` | Entidades que informan IRRECUP_DISP_TECNICA = 1 | COUNT entidades con el campo IRRECUP_DISP_TECNICA = 1 en t | nunca (9 = no aplica cuenta como 0) | + | futuro |  |  |  |  |
| `CEN_FLG_GARANTE_1M` | Figura como garante (garantías otorgadas > 0) en alguna entidad | SUM(garantias_otorgadas) > 0 en t | nunca | ? | futuro |  |  |  |  |
| `CEN_PCT_DEUDA_GTIA_PREF_1M` | Proporción de la deuda con garantía preferida A o B | SUM(gtia_pref_a + gtia_pref_b) / SUM(prestamos) en t | sin préstamos en el mes | - | futuro |  |  |  |  |

Notas:
- `CEN_MAX_DIAS_ATRASO_1M`: Lo informan 162 de 545 entidades, casi sólo para situación 2+.
- `CEN_CNT_ENT_REFINANCIACION_1M`: Refinanciación, situación jurídica e irrecuperable sólo aplican a bancos y compañías financieras.
- `CEN_CNT_ENT_RECATEG_OBLIG_1M`: Refinanciación, situación jurídica e irrecuperable sólo aplican a bancos y compañías financieras.
- `CEN_CNT_ENT_SIT_JURIDICA_1M`: Refinanciación, situación jurídica e irrecuperable sólo aplican a bancos y compañías financieras.
- `CEN_CNT_ENT_IRRECUP_DISP_TECNICA_1M`: Refinanciación, situación jurídica e irrecuperable sólo aplican a bancos y compañías financieras.
- `CEN_FLG_GARANTE_1M`: Única aproximación a la codeudoría del CRC.

<a id="b09_persona"></a>
## B09_PERSONA — Atributos de la persona (padrón, 1DSF)

| Variable | Descripción | Definición | Nulos | Sentido | Uso | IV | Rec. | S1 | S2 |
|---|---|---|---|---|---|---|---|---|---|
| `CEN_VAL_EDAD_1M` | Edad en años a la fecha de observación (personas humanas) | años entre fecha_nac_contrato y el último día del mes de observación | persona jurídica o fecha no informada (1901-01-01) | - | modelo | 0,17 | SI | SI | NO |
| `CEN_VAL_ANTIG_EMPRESA_1M` | Años desde el contrato social a la fecha de observación (personas jurídicas) | años entre fecha_nac_contrato y el último día del mes de observación | persona humana o fecha no informada | - | modelo | 0,01 | NO | NO | NO |
| `CEN_CAT_TIPO_PERSONA_1M` | Persona humana o jurídica según prefijo del CUIT | prefijo 20/23/24/27 = HUMANA; 30/33/34 = JURIDICA; resto = OTRO | nunca | ? | modelo | 0,00 | NO | NO | NO |
| `CEN_FLG_ACTIVIDAD_ARCA_1M` | Tiene actividad registrada en ARCA (monotributo, autónomo, empleador) | actividad > 0 en el padrón | CUIT no encontrado en el padrón | - | modelo | 0,05 | NO | NO | NO |
| `CEN_CAT_SECTOR_ACTIVIDAD_1M` | Sector de la actividad principal registrada en ARCA | actividad agrupada a sección del nomenclador (primeros dígitos, ver Actividades_ARCA) | sin actividad registrada | ? | modelo | 0,12 | SI | SI | NO |
| `CEN_CAT_PROVINCIA_1M` | Provincia del domicilio fiscal | provincia del padrón (código ARCA 00-24) | CUIT no encontrado en el padrón | ? | modelo | 0,06 | NO | NO | NO |
| `CEN_CNT_MESES_SIT1_ININT_1M` | Meses en situación 1 ininterrumpida en todas las entidades, sin tope de 24 meses | Sólo en la fecha del corte del 1DSF: 0 si SIT_t >= 2; si tuvo situación 2+ dentro del 24DSF, meses desde esa última vez; si no, t - desde_sit1 (1DSF); si no figura en el 1DSF, meses de historia disponibles. | fechas de observación anteriores al corte del 1DSF | - | futuro |  |  |  |  |
| `CEN_FLG_FALLECIDO_1M` | Persona fallecida a la fecha de observación | fecha_fallecimiento <= fin del mes de observación (19010101 = sucesión sin fecha, se considera fallecido) | nunca | + | exclusion |  |  |  |  |
| `CEN_FLG_CUIT_REEMPLAZADO_1M` | La historia incluye un CUIT dado de baja que fue reemplazado por el actual | existe cuit_baja con cuit_reemplazo = CUIT y deuda informada bajo cuit_baja en t-11..t | nunca | ? | modelo | 0,00 | NO | NO | NO |
| `CEN_CAT_SEXO_1M` | Sexo registrado en ARCA | sexo del padrón (M, F, sin registrar) | sin registrar | n/a | monitoreo | 0,00 | NO | NO | NO |

Notas:
- `CEN_FLG_ACTIVIDAD_ARCA_1M`: Foto del padrón (20261030); para fechas de observación pasadas puede filtrar información futura. Medir PSI y poder por fecha.
- `CEN_CAT_SECTOR_ACTIVIDAD_1M`: Misma salvedad de foto que CEN_FLG_ACTIVIDAD_ARCA_1M.
- `CEN_CNT_MESES_SIT1_ININT_1M`: El 1DSF es una foto del corte (202609). En fechas de observación anteriores filtra el desempeño (quien cae en situación 3+ después de t ya no figura), por eso sólo se calcula en la fecha del propio corte. Para desarrollo se usa CEN_CNT_RACHA_SIT1_1M. Entra al modelo cuando haya 1DSF archivados por mes.
- `CEN_FLG_FALLECIDO_1M`: Se excluye de la población de modelado; 219 mil deudores con deuda vigente, 79% en situación 3+.
- `CEN_CAT_SEXO_1M`: No entra al modelo (riesgo de discriminación y regulatorio). Sólo para medir sesgos del score.

<a id="b10_alertas"></a>
## B10_ALERTAS — Combinaciones de señales tempranas

| Variable | Descripción | Definición | Nulos | Sentido | Uso | IV | Rec. | S1 | S2 |
|---|---|---|---|---|---|---|---|---|---|
| `CEN_FLG_ALERTA_DETERIORO_3M` | Pasó de situación 1 a situación 2 o peor en los últimos 3 meses | SIT_t >= 2 AND SIT_(t-2) = 1 | nunca | + | modelo | 1,25 | NO | NO | NO |
| `CEN_FLG_ALERTA_CONTAGIO_3M` | Entró en situación 2 o peor en dos o más entidades en los últimos 3 meses | CEN_CNT_ENT_EMPEORA_3M >= 2 AND ENT_IRR_t >= 2 | nunca | + | modelo | 0,28 | SI | NO | NO |
| `CEN_FLG_ALERTA_SOBREENDEUDAMIENTO_6M` | Deuda creciendo fuerte con relaciones nuevas | CEN_TEN_DEUDA_6M >= 1.5 AND CEN_CNT_ALTAS_6M >= 2 | nunca | + | modelo | 0,07 | NO | NO | NO |
| `CEN_FLG_ALERTA_REFUGIO_NO_BANCARIO_6M` | Cerró relaciones bancarias y abrió no bancarias en los últimos 6 meses | bajas con BANCO o CIA_FIN >= 1 AND altas con TARJETA_NB, PNFC o FIDEICOMISO >= 1 en t-5..t | nunca | + | modelo | 0,03 | NO | NO | NO |
| `CEN_FLG_ALERTA_REINCIDENCIA_12M` | Se curó de situación 2+ y volvió a caer en los últimos 12 meses | secuencia SIT >= 2, luego SIT = 1, luego SIT >= 2 dentro de t-11..t | nunca | + | modelo | 0,26 | SI | NO | NO |
| `CEN_CNT_ALERTAS_1M` | Cantidad de alertas activas | suma de las CEN_FLG_ALERTA_* | nunca | + | modelo | 0,96 | SI | SI | NO |

Notas:
- `CEN_FLG_ALERTA_SOBREENDEUDAMIENTO_6M`: Umbrales iniciales; se calibran con el informe de poder.

<a id="b11_cheques"></a>
## B11_CHEQUES — Cheques rechazados propios y de las empresas vinculadas

| Variable | Descripción | Definición | Nulos | Sentido | Uso | IV | Rec. | S1 | S2 |
|---|---|---|---|---|---|---|---|---|---|
| `CEN_CNT_CHQ_RECH_SF_3M` | Cheques propios rechazados sin fondos en los últimos 3 meses | COUNT cheques propios con causal 2 y fecha de rechazo en t-2..t | sin foto de cheques para el mes de observación | + | futuro |  |  |  |  |
| `CEN_CNT_CHQ_RECH_SF_6M` | Cheques propios rechazados sin fondos en los últimos 6 meses | COUNT cheques propios con causal 2 y fecha de rechazo en t-5..t | sin foto de cheques para el mes de observación | + | futuro |  |  |  |  |
| `CEN_CNT_CHQ_RECH_SF_12M` | Cheques propios rechazados sin fondos en los últimos 12 meses | COUNT cheques propios con causal 2 y fecha de rechazo en t-11..t | sin foto de cheques para el mes de observación | + | futuro |  |  |  |  |
| `CEN_SUM_CHQ_RECH_SF_3M` | Monto de los cheques propios rechazados sin fondos en los últimos 3 meses | SUM monto de los cheques propios con causal 2 y fecha de rechazo en t-2..t, deflactado con el IPC del mes del rechazo | sin foto de cheques para el mes de observación | + | futuro |  |  |  |  |
| `CEN_SUM_CHQ_RECH_SF_6M` | Monto de los cheques propios rechazados sin fondos en los últimos 6 meses | SUM monto de los cheques propios con causal 2 y fecha de rechazo en t-5..t, deflactado con el IPC del mes del rechazo | sin foto de cheques para el mes de observación | + | futuro |  |  |  |  |
| `CEN_SUM_CHQ_RECH_SF_12M` | Monto de los cheques propios rechazados sin fondos en los últimos 12 meses | SUM monto de los cheques propios con causal 2 y fecha de rechazo en t-11..t, deflactado con el IPC del mes del rechazo | sin foto de cheques para el mes de observación | + | futuro |  |  |  |  |
| `CEN_CNT_CHQ_RECH_VF_12M` | Cheques propios rechazados por vicios formales en los últimos 12 meses | COUNT cheques propios con causal 1 y fecha de rechazo en t-11..t | sin foto de cheques para el mes de observación | ? | futuro |  |  |  |  |
| `CEN_CNT_CHQ_IMPAGOS_1M` | Cheques propios rechazados sin fondos que siguen sin levantar a la fecha de observación | COUNT cheques propios con causal 2, rechazados hasta t y sin fecha de levantamiento hasta el último día de t | sin foto de cheques para el mes de observación | + | futuro |  |  |  |  |
| `CEN_MTO_CHQ_IMPAGOS_1M` | Monto de los cheques propios rechazados sin fondos sin levantar a la fecha de observación | SUM monto de los cheques de CEN_CNT_CHQ_IMPAGOS_1M, deflactado con el IPC del mes del rechazo | sin foto de cheques para el mes de observación | + | futuro |  |  |  |  |
| `CEN_PCT_CHQ_LEVANTADOS_12M` | Proporción de los cheques propios rechazados sin fondos en los últimos 12 meses que ya se levantaron | cheques con causal 2 rechazados en t-11..t y levantados hasta el último día de t / cheques con causal 2 rechazados en t-11..t | sin rechazos sin fondos en los últimos 12 meses o sin foto de cheques | - | futuro |  |  |  |  |
| `CEN_RCM_CHQ_RECH_SF_12M` | Meses desde el último rechazo sin fondos de un cheque propio en los últimos 12 meses | t - mes del último rechazo con causal 2 en t-11..t | sin rechazos sin fondos en los últimos 12 meses o sin foto de cheques | - | futuro |  |  |  |  |
| `CEN_FLG_CHQ_MULTA_IMPAGA_1M` | Tiene algún cheque propio rechazado sin fondos con la multa impaga a la fecha de observación | algún cheque propio con causal 2 y pago de multa IMPAGA o pagada después del último día de t | sin foto de cheques para el mes de observación | + | futuro |  |  |  |  |
| `CEN_RAT_CHQ_RECH_DEUDA_12M` | Monto rechazado sin fondos en los últimos 12 meses sobre la deuda en el sistema | CEN_SUM_CHQ_RECH_SF_12M / CEN_MTO_DEUDA_1M | deuda 0 o sin foto de cheques | + | futuro |  |  |  |  |
| `CEN_CNT_CHQ_REL_RECH_SF_12M` | Cheques rechazados sin fondos en los últimos 12 meses de las personas jurídicas vinculadas | COUNT cheques con causal 2 y fecha de rechazo en t-11..t informados con el CUIT en una persona jurídica relacionada | sin foto de cheques para el mes de observación | + | futuro |  |  |  |  |
| `CEN_CNT_EMP_REL_CHQ_12M` | Personas jurídicas vinculadas con cheques rechazados sin fondos en los últimos 12 meses | COUNT DISTINCT CUIT relacionado con algún cheque con causal 2 en t-11..t | sin foto de cheques para el mes de observación | + | futuro |  |  |  |  |
| `CEN_CNT_CHQ_REL_IMPAGOS_1M` | Cheques sin fondos sin levantar de las personas jurídicas vinculadas a la fecha de observación | COUNT cheques con causal 2 de personas jurídicas relacionadas, sin levantar hasta el último día de t | sin foto de cheques para el mes de observación | + | futuro |  |  |  |  |

Notas:
- `CEN_CNT_CHQ_RECH_VF_12M`: Causal poco frecuente (1,3% de la foto 20261031); no indica falta de fondos.
- `CEN_CNT_CHQ_IMPAGOS_1M`: Toda la historia de la foto (hasta 5 años); el BCRA da de baja los cheques levantados viejos, no los impagos.
- `CEN_MTO_CHQ_IMPAGOS_1M`: Los rechazos anteriores al primer mes del IPC versionado (202409) se deflactan con ese mes, así que su monto queda subestimado.
- `CEN_FLG_CHQ_MULTA_IMPAGA_1M`: Las multas suspendidas (Ley 24.452, art. 64) no cuentan como impagas.
- `CEN_CNT_CHQ_REL_RECH_SF_12M`: Contagio desde las empresas en las que la persona figura como relacionada (por ejemplo, socio o directivo).

## Cómo agregar o cambiar una variable

1. Editar `catalogo/variables.yaml` (o agregar un valor a `expandir` de una familia existente).
2. `cendeu catalogo` valida nombre, operación, bloque, fuente, tipo y uso.
3. Agregar el caso a `tests/test_v2.py` si la lógica es nueva.
4. `cendeu documentar` regenera este documento y el diccionario.
