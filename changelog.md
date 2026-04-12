# Changelog

## 2026-04-12 — Config óptima + limpieza repo

### Cambios
- **accounts.yaml actualizado** con config óptima del sweep:
  - Exam: P1 risk 4% (antes 2%), P2 risk 1.5% (antes 1%), DC3.5, MI2, ML3
  - p2_risk_factor: 0.375 (0.04 * 0.375 = 0.015)
  - Funded: sin cambios (1% risk, DC2.5)
  - Deck: 25 combos ALIVE del pool v7_expanded (antes 24 combos v5 con naming mismatches)
  - symbol_map: añadidos AUDUSD, NZDUSD, EURJPY
- **Imports actualizados a v7_expanded**: live_trader.py, check_signals.py, check_last_week.py, validate_live.py, test_live_parity.py
- **18 scripts obsoletos eliminados**:
  - Deck files: challenge_decks.py, challenge_decks_v4_pool.py, challenge_decks_v5_clean.py, .bak_preaudit
  - Scripts con imports a decks viejos: adaptive_deck, audit_biases, diagnose_2025, personal_account_sim, rebuild_all_combos, walk_forward, validate_combos, validate_phase34
  - Reemplazados por production_sim/optimize_exam_params: oos_2026_ftmo, optimize_deck, strategy_scan
  - One-time/obsoletos: download_dukascopy, compare_dukascopy_ftmo, check_ftmo_history_depth

### Scripts que quedan (11)
| Script | Función |
|--------|---------|
| challenge_decks_v7_expanded.py | Pool activo (53 combos) |
| production_sim.py | Validación producción IS+OOS |
| optimize_exam_params.py | Sweep parámetros exam |
| diagnose_combo_health.py | Diagnóstico año-por-año |
| live_trader.py | Trading live MT5 |
| validate_live.py | Validación paridad live/backtest |
| check_signals.py | Monitoreo señales |
| check_last_week.py | Monitoreo semanal |
| download_ftmo_data.py | Descarga datos FTMO 2026 |
| massive_scan.py | Scan masivo nuevos combos |
| deduplicate_pool.py | Deduplicación por señal |

### Qué soluciona
- Zero deuda técnica: no queda código que referencie pools obsoletos.
- Config live alineada con los mejores resultados del sweep (IS 50.2%, OOS 10.2%).
- Repo limpio y mantenible.

---

## 2026-04-12 — Diagnóstico combo health + deck ALIVE filtrado

### Cambios
- **Nuevo script `scripts/diagnose_combo_health.py`**: Ejecuta los 53 combos del pool v7_expanded año por año (2016-2025) y clasifica cada combo como ALIVE/DYING/DEAD según PF en 2024-2025.
- **`scripts/production_sim.py` actualizado a pool v7_expanded**: Cambiado import de `challenge_decks_v5_clean` a `challenge_decks_v7_expanded`. El deck anterior (v5) tenía naming mismatches con el pool real.
- **Deck ALIVE de 25 combos**: Reemplaza el placeholder deck de 35 combos obsoletos. Contiene solo combos con PF > 1.0 en 2024-2025, divididos en Tier 1 (>=6 yrs profitable, 16 combos) y Tier 2 (4-5 yrs, 9 combos).
- **Fix cálculo OOS end date**: `last_data_date` ahora usa `min()` solo de symbols con datos 2026 (FTMO). Antes usaba `min()` global, y USDJPY (sin datos 2026) tiraba el OOS a 2025-10-03, dejando 0 starts OOS.
- **Deck ya no se carga de accounts.yaml en production_sim**: El deck de accounts.yaml tiene nombres que no matchean v7_expanded (ej: `MACross_XAUUSD_trend_H4` vs `MACross_XAUUSD_trend_H4_ny`). Se usa el deck hardcoded ALIVE hasta sincronizar accounts.yaml.

### Resultados
- **Deck anterior (accounts.yaml)**: 33% health (8/24 ALIVE), naming mismatches con pool v7.
- **Deck ALIVE (25 combos)**: 100% health. IS funded rate 49.6%, avg 36 días. OOS P1 pass 28.6% (subió de 0%), pero P2 0% (alpha se seca post-febrero 2026).
- **Funded survival**: 16.6 meses avg, 21% terminación, EUR +231/mo net por $5K.
- **Problema OOS**: Enero 2026 tiene crash masivo (-11.7% DD), febrero pasa P1 pero P2 falla por falta de alpha sostenido en marzo.

### Qué soluciona
- Elimina combos muertos del deck que estaban drenando performance.
- Identifica qué estrategias/instrumentos tienen edge vivo (NZDUSD, VMR, MACross top).
- Corrige bug de OOS date que impedía validar con datos FTMO 2026.

---

## 2026-04-12 — Simulación portfolio-level + datos FTMO 2026

### Cambios
- **`scripts/production_sim.py` reescrito con position sizing real**: Trades pre-computados guardan datos pip-level (pnl_pips, sl_pips, pip_value, min_lot, max_lot). En replay, cada trade se re-dimensiona sobre equity real del portfolio. Antes usaba PnL en $ pre-computado con risk_factor flat — no reflejaba el sizing real.
- **Nuevo script `scripts/download_ftmo_data.py`**: Descarga M1 de FTMO MT5 para 8 símbolos (EURUSD, GBPJPY, USDCHF, XAUUSD, XTIUSD, AUDUSD, NZDUSD, EURJPY). Periodo: 2026-01-02 a 2026-04-10. ~95K-101K bars por símbolo.
- **Monte Carlo every-business-day**: Reemplaza ventanas fijas cada 30 días. IS = 2,522 business days, OOS = 49 business days.
- **Config cambiada de risk_factor a risk_per_trade directo**: `EXAM_CONFIG` usa `risk_per_trade` y `p2_risk_per_trade` en vez de multiplicadores sobre base.

### Qué soluciona
- El backtest ahora refleja cómo opera el portfolio en realidad: equity compartida, sizing proporcional, comisiones por lote real.
- OOS con datos FTMO reales (no simulados) — spread y condiciones de mercado reales del broker.
- Sampling estadístico robusto con cada día hábil como posible inicio de examen.

---

## 2026-04-12 — Pool v7_expanded (53 combos deduplicados)

### Cambios
- **`scripts/challenge_decks_v7_expanded.py`**: Pool limpio de 53 combos tras scan masivo + deduplicación por señal (39 redundantes eliminados, threshold subset>80%, clone>90%).
- 17 tipos de estrategia: ADXbirth, CCIext, EMArib, Engulf, IBB, KeltSq, MACDhist, MACross, MomDiv, PinBar, RSIext, RegVMR, StochRev, StrBrk, SwBrk, TPB, VMR.
- 10 instrumentos: AUDUSD, EURJPY, EURUSD, GBPJPY, NZDUSD, USDCAD, USDCHF, USDJPY, XAUUSD, XTIUSD.

### Qué soluciona
- Pool sin trades duplicados entre combos (requisito CLAUDE.md).
- Base para selección de deck con filtros de recencia y consistencia.
