# PARITY-20260412-baseline

## Elementos equivalentes confirmados

- `tests/test_live_runtime.py` confirma, a nivel unitario, límites de posiciones por cuenta, recuperación de posiciones MT5, persistencia de estado runtime, frontera configurable de día de trading y transiciones `phase1 -> phase2 -> funded`.
- `scripts/live_trader.py::LiveAccount.evaluate_signal()` y `algosbz/backtest/engine.py::BacktestEngine.run()` comparten el patrón clave de ejecución: señal en barra cerrada, evaluación/sizing en la siguiente apertura, y uso de `RiskManager` con multiplicador del `EquityManager`.

## Divergencias detectadas

- `python -m pytest -q tests/test_live_parity.py` no completó en la ventana de 304 segundos usada en esta baseline.
- `python -X utf8 scripts/validate_live.py --offline` ejecutó sin `MISMATCH`, pero reportó `0 matched`, `3 live-only` y trabajó sobre un log que contiene eventos de test del account `TEST`.

## Posibles causas

- El test de parity de deck completo carga datos y resamplea múltiples feeds, por lo que no funciona como smoke test rápido.
- El validador offline depende de `data/trade_history.jsonl`; si el log está contaminado, el resultado deja de ser evidencia fuerte de equivalencia.
- El propio validador compara principalmente tiempo, dirección y SL; no cierra equivalencia completa de volumen, TP, PnL ni motivo de rechazo.

## Checks o logs que faltan

- Snapshot de config activa por trade: fase, `exam_mode`/`funded_mode`, `risk_per_trade`, caps y cooldown vigentes.
- Razón explícita de cada señal rechazada en live para distinguir portfolio control de bug de lógica.
- Comparación sistemática de `volume`, `tp`, `fill_price`, `gap` y PnL realizado frente a lo modelado.

## Riesgo operativo de cada divergencia

- Timeout del test de parity: riesgo medio. Reduce capacidad de usar parity como puerta rápida antes de despliegue.
- Log contaminado: riesgo alto. Puede dar una falsa sensación de alineación o esconder divergencias reales.
- Cobertura parcial del validador: riesgo medio-alto. Permite detectar bugs gruesos, pero no garantiza equivalencia económica completa.

## Impacto sobre fase de evaluación o cuenta fondeada

- En `phase1` y `phase2`, una divergencia de timing o sizing puede degradar tasa de aprobación y violar límites sin estar reflejado en research.
- En `funded`, la misma divergencia puede reducir EV, acelerar pérdida de cuenta o impedir aprovechar el colchón operativo.

## Plan de corrección

1. Mantener aislados los logs de tests para no volver a contaminar `trade_history.jsonl`.
2. Tratar la baseline offline actual como evidencia parcial hasta limpiar el log histórico.
3. Añadir una puerta de parity rápida sobre un subconjunto pequeño de combos y rango temporal corto.
4. Enriquecer `validate_live.py` para comparar también volumen, TP, fill y contexto de fase.

## Evidencia usada

- `Código`: [`algosbz/backtest/engine.py`](../../algosbz/backtest/engine.py), [`scripts/live_trader.py`](../../scripts/live_trader.py), [`scripts/validate_live.py`](../../scripts/validate_live.py).
- `Test`: [`tests/test_live_runtime.py`](../../tests/test_live_runtime.py), [`tests/test_live_parity.py`](../../tests/test_live_parity.py).
- `Resultado`: ejecución local de `python -m pytest -q tests/test_live_runtime.py`, `python -m pytest -q tests/test_live_parity.py` y `python -X utf8 scripts/validate_live.py --offline` el 2026-04-12.
- `Hipótesis`: el timeout del parity test probablemente refleja coste de carga/cómputo, no necesariamente un bug funcional.
