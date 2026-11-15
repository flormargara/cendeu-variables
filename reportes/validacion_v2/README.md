# Validación multivariada de las variables v2

Fase 6 del [plan v2](../../PLAN.md). El [informe de poder](../poder_v2/) mide cada variable por separado; esta validación responde lo que ese informe no puede: si las variables **juntas** suman poder, si ese poder **se sostiene fuera de muestra** y si **superan a las variables v1**.

> Este repo construye variables. El ajuste de abajo es sólo una prueba de las variables, no un modelo de PD: no está calibrado ni pensado para producción. Un modelo de PD se desarrolla en otro proyecto, con sus propias decisiones de población, target, selección y calibración.

- **Código**: [`src/cendeu/validacion_v2.py`](../../src/cendeu/validacion_v2.py), comando `cendeu validar-v2`.
- **Resultados**: [`ajuste_sit1.json`](ajuste_sit1.json) y [`ajuste_sit2.json`](ajuste_sit2.json), con bins, métricas por fecha, comparación con la v1 y la traza de selección (qué variable entró, cuál se descartó y por qué).

## Cómo se hizo

- **Método**: el de la v1 (binning monótono, WoE, selección forward por ganancia de AUC con filtros de correlación > 0,7 y de signo, regresión logística, hasta 12 variables). Candidatas: las 136 variables numéricas con uso `modelo` del catálogo.
- **Segmentos**: situación 1 y situación 2 en el mes de observación, porque son poblaciones distintas (tasa de target de ~6% y ~75%).
- **Fechas**: desarrollo 202510–202601 (70% para ajustar, 30% para elegir variables, por hash del CUIT); fuera de muestra 202602 y 202603. 202509 queda afuera porque ahí no existen las variables de 12 meses que necesitan el mes previo.
- **Muestra**: fija por hash del CUIT, la misma en todas las fechas: 3% en situación 1 (~450 mil por fecha) y 30% en situación 2 (~250 mil por fecha).

## Resultados

| | Situación 1 | Situación 2 |
|---|---|---|
| Tasa de target (desarrollo) | 6,5% | 77,3% |
| Variables elegidas | 11 | 12 |
| Gini test (desarrollo) | 0,601 | 0,482 |
| **Gini fuera de muestra** | **0,566** | **0,463** |
| KS fuera de muestra | 0,429 | 0,341 |
| PSI del score, máximo por fecha | 0,004 | 0,030 |

**Gini por fecha**

| Fecha | Muestra | Situación 1 | Situación 2 |
|---|---|---|---|
| 202510 | desarrollo | 0,605 | 0,483 |
| 202511 | desarrollo | 0,592 | 0,482 |
| 202512 | desarrollo | 0,597 | 0,479 |
| 202601 | desarrollo | 0,590 | 0,479 |
| 202602 | fuera de muestra | 0,571 | 0,463 |
| 202603 | fuera de muestra | 0,561 | 0,463 |

El poder baja 2 a 3 puntos al pasar a fechas no vistas y se mantiene parejo entre ellas. La distribución del score casi no se mueve (PSI < 0,03).

### Variables v2 contra variables v1

Mismas personas (humanas en situación 1, población del scorecard v1), target a 6 meses:

| Fecha | ¿Fuera de muestra para la v1? | Gini v1 | Gini v2 | KS v1 | KS v2 |
|---|---|---|---|---|---|
| 202603 | sí (y para la v2) | 0,532 | **0,564** | 0,406 | **0,426** |
| 202509 | no: la v1 se entrenó en esta fecha | **0,584** | 0,575 | **0,453** | 0,430 |

La comparación válida es **202603**, que ninguno de los dos ajustes vio: las variables v2 discriminan **3 puntos de Gini más** que las v1. En 202509 la comparación favorece a la v1, que se entrenó ahí, y perjudica a la v2, que usa la tasa de cura a 12 meses, nula en esa fecha. La v1 se entrenó con target a 12 meses y se mide a 6, lo que le resta algo; no alcanza para explicar toda la diferencia.

## Qué variables aportan en conjunto

**Situación 1** (11): `CEN_RSI_DEUDA_12M`, `CEN_RSI_ENT_12M`, `CEN_PCT_DEUDA_PNFC_1M`, `CEN_MAX_SIT_NO_BANCARIA_12M`, `CEN_CNT_RACHA_SIT1_1M`, `CEN_RAT_CURA_12M`, `CEN_CNT_ENT_1M`, `CEN_TEN_DEUDA_PNFC_6M`, `CEN_VAL_EDAD_1M`, `CEN_VAR_DEUDA_1M`, `CEN_FLG_ACTIVIDAD_ARCA_1M`.

**Situación 2** (12): `CEN_RSI_DEUDA_12M`, `CEN_CNT_ENT_EMPEORA_3M`, `CEN_FMX_DEUDA_6M`, `CEN_RSI_DEUDA_3M`, `CEN_TEN_ENT_12M`, `CEN_VAR_DEUDA_1M`, `CEN_RCM_ULTIMA_ALTA_12M`, `CEN_PCT_DEUDA_PNFC_1M`, `CEN_VAL_EDAD_1M`, `CEN_RAT_CURA_12M`, `CEN_AVG_PCT_DEUDA_IRR_3M`, `CEN_CNT_MESES_SIT2P_3M`.

- El **momentum (RSI)** entra primero en los dos segmentos: es la señal más fuerte que no se reduce a la situación actual.
- El **mix por tipo de entidad** (peso y tendencia de la deuda con PNFC, peor situación no bancaria) y las **transiciones** (racha en situación 1, tasa de cura) aportan información que la v1 no tenía.
- Casi no entran variables de magnitud y calidad en situación 1: dentro del segmento la situación ya está fija y su señal queda cubierta por las de momentum y transiciones.
- De las fuentes nuevas entran la **edad** (padrón) y la **actividad registrada en ARCA**. Esta última es una foto de 20261030: aporta poco (+0,002 de Gini) y conviene revisarla en el proyecto de modelos.

## Para el proyecto de modelos

- Separar situación 1 y situación 2.
- Usar fechas de observación desde 202510 para las variables de 12 meses que necesitan el mes previo, o tratar sus nulos en 202509.
- La tasa de target baja de una fecha a la siguiente: un modelo de PD necesita recalibración por cohorte.
- Las variables `futuro` (foto del mensual y meses en situación 1 según el 1DSF) quedan disponibles a medida que se acumulen cortes mensuales.
