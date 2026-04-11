# Auditoria del sistema de trading

Fecha: 2026-04-06

## Resumen ejecutivo

La base esta mejor que en la auditoria anterior. He verificado que varios fallos criticos ya estan corregidos:

- `scripts/optimize_deck.py` ya selecciona la mejor configuracion por `IS` y deja `OOS` como validacion.
- `algosbz/backtest/engine.py` ya mueve la entrada a la barra de ejecucion y corrige el `timestamp`.
- `algosbz/backtest/broker.py` ya trata mejor los gaps en `SL` y usa `spread: first` tras el resample.
- `scripts/live_trader.py` ya refresca la vela que estaba formandose antes de anadir la nueva.
- `scripts/live_trader.py` ya usa `place_market_order()` y no mantiene el antiguo fallo de llamada a metodo inexistente.

Aun asi, siguen abiertos varios problemas serios. Los dos mas importantes estan en el live:

1. El limite de `max_positions` no se aplica a nivel cuenta.
2. Un reinicio puede hacer que el sistema pierda el rastro de posiciones reales ya abiertas en MT5.

## Hallazgos abiertos

### 1. CRITICO - `max_positions` no se aplica a nivel cuenta

**Impacto**

El live puede abrir mas posiciones simultaneas de las que el modelo de riesgo pretende permitir. Esto aumenta el DD real frente al DD esperado y rompe la equivalencia entre el riesgo modelado y el riesgo ejecutado.

**Evidencia**

- `scripts/live_trader.py:350-362` crea un `RiskManager` por simbolo, no por cuenta.
- `scripts/live_trader.py:367-406` evalua la senal contra el `RiskManager` del simbolo actual.
- `scripts/live_trader.py:429-436` incrementa `open_position_count` solo en el `RiskManager` de ese simbolo.
- `algosbz/live/account_manager.py:111-143` no tiene ningun control de maximo global de posiciones abiertas.
- `algosbz/risk/manager.py:63-65` si intenta limitar `max_positions`, pero ese contador queda particionado por simbolo.

**Verificacion local**

He reproducido el caso: con 3 posiciones ya abiertas en `EURUSD`, `XAUUSD` y `XTIUSD`, el sistema sigue aprobando una cuarta entrada en `USDCHF`.

**Recomendacion**

- Mover `max_positions` a un control de cuenta unico.
- O bien mantener un `RiskManager` unico por cuenta para el conteo de posiciones.
- Anadir test que abra 3 posiciones en simbolos distintos y confirme que la cuarta se rechaza.

### 2. CRITICO - Tras un reinicio, el live puede perder posiciones reales abiertas y duplicarlas

**Impacto**

Si el proceso cae despues de ejecutar una orden pero antes del siguiente `save_state()`, al arrancar de nuevo el sistema puede no saber que esa posicion ya existe en MT5. Eso permite volver a entrar en el mismo combo o dejar una posicion viva fuera del control del bot.

**Evidencia**

- `scripts/live_trader.py:473-511` persiste `open_positions` solo en el fichero de estado.
- `scripts/live_trader.py:514-554` restaura `open_positions` solo desde ese fichero.
- `scripts/live_trader.py:845-859` sincroniza cierres solo para cuentas que ya tengan `open_positions` restauradas.
- `scripts/live_trader.py:940-941` guarda estado antes de ejecutar ordenes.
- `scripts/live_trader.py:1012-1055` ejecuta ordenes.
- `scripts/live_trader.py:1059-1060` guarda estado despues de ejecutar.

Entre `1012` y `1060` hay una ventana real donde una orden puede quedar abierta en MT5 pero no persistida localmente.

**Recomendacion**

- En el arranque, descubrir siempre las posiciones abiertas reales desde MT5 y reconciliarlas con el estado local.
- No depender solo del fichero `live_state.json` como fuente de verdad.
- Guardar estado inmediatamente despues de cada fill confirmado, no solo al final del ciclo.

### 3. ALTO - El reset diario y la zona horaria siguen sin estar definidos de forma operativa

**Impacto**

El sistema puede resetear el dia en una hora distinta a la del broker/prop firm, contar mal los trading days y aplicar filtros horarios/sesiones sobre timestamps ingenuos. Eso afecta tanto al control de DD diario como a la paridad backtest/live.

**Evidencia**

- `config/default.yaml:10` define `daily_reset_hour`, pero no se usa en runtime.
- `algosbz/live/account_manager.py:84-90` usa `date.today()`.
- `scripts/live_trader.py:774-800` usa `datetime.now().date()` para el cambio de dia.
- `algosbz/live/mt5_connector.py:96-114` convierte barras a timestamps naive.
- Estrategias con filtros de sesion usan `data.index.hour` directamente, por ejemplo:
  - `algosbz/strategy/session_breakout_v2.py:57-58`
  - `algosbz/strategy/ema_ribbon_trend.py:57`
  - `algosbz/strategy/smc_order_block.py:89`

**Recomendacion**

- Elegir explicitamente una timezone canonica para backtest y live.
- Aplicar `daily_reset_hour` de forma real en el runtime.
- Normalizar timestamps con timezone antes de cualquier logica de sesiones, trading day o DD diario.

### 4. ALTO - El validador live-vs-backtest no es una prueba fiable en su estado actual

**Impacto**

Hoy no puedes afirmar con rigor que el live replica el backtest usando `validate_live.py`, porque la herramienta mezcla eventos de apertura y cierre, y el productor del log ya no esta registrando bien las aperturas.

**Evidencia**

- `scripts/validate_live.py:46-68` carga cualquier linea del log.
- `scripts/validate_live.py:157-160` agrupa todas esas lineas por combo sin filtrar `BUY/SELL` frente a `CLOSE`.
- `scripts/validate_live.py:125` reconstruye senales con `has_position=False`, asi que no reproduce controles reales de cartera.
- `scripts/validate_live.py:251` acepta emparejamientos con hasta 8 horas de tolerancia.
- `scripts/live_trader.py:557-561` define el log.
- `scripts/live_trader.py:613-620` actualmente solo escribe cierres en el log desde este flujo.

**Verificacion local**

El `trade_history.jsonl` actual contiene eventos `BUY` y `CLOSE`, y `load_live_trades()` los carga todos como si fueran comparables contra senales de entrada.

**Recomendacion**

- Registrar aperturas y cierres como eventos distintos y tipados.
- Filtrar el validador para comparar solo aperturas contra aperturas.
- Comparar tambien ticket, cuenta, barra de fill y SL/TP ajustados.
- Reducir la ventana de matching al timeframe real de cada estrategia.

### 5. ALTO - `adaptive_deck.py` sigue contaminando el OOS si se usa para elegir despliegue

**Impacto**

Aunque `optimize_deck.py` ya esta corregido, `scripts/adaptive_deck.py` sigue ordenando y recomendando configuraciones por `oos_rate`. Si este script se usa para decidir que desplegar, el OOS 2025 deja de ser una validacion limpia.

**Evidencia**

- `scripts/adaptive_deck.py:366-367` ordena `grid_results` por `oos_rate`.
- `scripts/adaptive_deck.py:387-390` toma `viable[0]` como configuracion recomendada.
- `scripts/adaptive_deck.py:392` presenta ese mismo `oos_rate` como "Pure 2025 OOS".

**Recomendacion**

- Si se mantiene el script, seleccionar por `walk-forward/IS` y dejar `OOS` solo para validacion.
- O marcar el script explicitamente como exploratorio/no apto para seleccionar configuraciones de produccion.

### 6. MEDIO - La traza operativa de trades sigue siendo debil para auditoria forense

**Impacto**

Aunque exista `trade_history.jsonl`, hoy no es una fuente robusta para reconstruir exactamente que paso en live: el `ts` es hora local del log, no timestamp oficial del fill MT5, y ya hay ejemplos historicos con `fill_price: 0.0`.

**Evidencia**

- `scripts/live_trader.py:557-561` graba `datetime.now().isoformat()`.
- El `trade_history.jsonl` actual contiene una apertura con `fill_price: 0.0`.

**Recomendacion**

- Guardar `mt5 fill time`, `requested price`, `filled price`, `sl`, `tp`, `ticket`, `account`, `combo`, `bar_time`, `signal_time` y `execution_time`.
- No usar la hora local del proceso como unica referencia temporal.

### 7. MEDIO - Credenciales MT5 en claro dentro de `config/accounts.yaml`

**Impacto**

Es un riesgo operativo y de seguridad. Si el repo se copia, se comparte o se sube por error, las cuentas quedan expuestas.

**Evidencia**

- `config/accounts.yaml` contiene credenciales reales en texto plano.

**Recomendacion**

- Mover secretos a variables de entorno o a un secret store.
- Dejar en el repo solo un `accounts.example.yaml`.

## Prioridad recomendada para el equipo

1. Arreglar `max_positions` a nivel cuenta.
2. Anadir reconciliacion obligatoria de posiciones MT5 al arrancar.
3. Definir timezone unica y hacer efectivo `daily_reset_hour`.
4. Reparar el pipeline de auditoria live: logging de aperturas + validador.
5. Limpiar `adaptive_deck.py` o etiquetarlo como no desplegable.

## Conclusion

El motor de backtest esta bastante mas limpio que en la revision anterior. El problema ahora no esta tanto en "trampas" del motor, sino en que el live y la capa de validacion todavia tienen huecos operativos importantes. Antes de escalar cuentas o volumen, yo cerraria al menos los cuatro primeros puntos.
