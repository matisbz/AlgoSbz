ya # Changelog

## 2026-04-13 — Recalibración completa con datos FTMO broker-native

### Problema
Los datos de Dukascopy (EURJPY, NZDUSD, AUDUSD) divergen 6-14 pips de FTMO y tienen spread=0, inflando el funded rate en backtest. Cross-validación mostró: Darwinex 46.7% funded vs FTMO 24.0% para mismos combos en 2024 — 22pp de inflación ficticia.

### Cambios
- **Datos reemplazados**: Descargados 10 archivos `*_M1_FTMO_full.csv` (2015-2026, ~4M bars cada uno) vía `download_ftmo_full_history.py`. DataLoader prioriza FTMO_full sobre Darwinex/Dukascopy.
- **Pipeline completo re-ejecutado con FTMO data**:
  1. `diagnose_combo_health.py` → 23 ALIVE (antes 25 con datos viejos)
  2. `massive_scan.py` → 40 ROBUST + 24 SPREAD_OK = 64 combos viables
  3. `deduplicate_pool.py` → 49 combos únicos (15 clones eliminados)
  4. `optimize_exam_params.py` → Walk-forward filter (32 supervivientes) + sweep 81 configs
  5. `production_sim.py` → Validación final
- **Nuevo pool v7_expanded**: 49 combos (16 estrategias, 9 instrumentos). Reemplaza pool anterior basado en datos con spread=0.
- **Config óptima actualizada**: P1R4%, P2R2.0% (antes P2R1.5%), DC3.5, MI3 (antes MI2), ML3, CD1.
- **accounts.yaml actualizado**: Nuevo deck de 32 combos (21 ROBUST + 11 SPREAD_OK walk-forward survivors), nueva config exam/funded, USDCAD añadido a instrumentos.
- **Scripts nuevos**: `download_ftmo_full_history.py`, `crossval_data_sources.py`.

### Resultados
| Métrica | Antes (Dukascopy) | Ahora (FTMO) |
|---------|-------------------|--------------|
| IS funded rate | 50.2% | 40.9% |
| OOS funded rate | 10.2% | 11.8% |
| IS avg days | 26d | 26d |
| Funded survival | 16.6mo | 14.1mo |
| Monthly net/$5K | EUR 231 | EUR 167 |
| Termination | 21% | 37% |
| Deck size | 25 combos | 32 combos |

### Qué soluciona
- Elimina sesgo de spread=0 de Dukascopy que inflaba PnL un 22pp.
- Backtest ahora usa exactamente los mismos datos que el broker live (FTMO).
- Números reales: sistema rentable a escala (10 exams/mo → EUR +2,405/mo net), breakeven en worst case.

---

## 2026-04-13 — Fix ejecución live: tick None + señal consumida en error

### Cambios
- **`mt5_connector.py` — `place_market_order` robusto post-reconexión**:
  - Añadido `symbol_select` antes del loop de retries (igual que `get_bars`).
  - Si `symbol_info_tick` devuelve None, espera 2s y reintenta (hasta max_retries). Antes fallaba inmediatamente.
  - Causa raíz: tras `mt5.shutdown()` + `mt5.initialize()`, los ticks no están disponibles inmediatamente. `get_bars` (datos históricos) funciona, pero `symbol_info_tick` (tick real-time) necesita que el terminal se suscriba al símbolo primero.
- **`live_trader.py` — señal NO se consume en error transitorio**:
  - Antes: si `place_market_order` fallaba, la señal se marcaba como consumida ("to match backtest"). Esto perdía el trade y causaba divergencia: el siguiente bar veía `has_position=False` y podía generar una señal duplicada que en backtest no existiría.
  - Ahora: la señal se mantiene pending para reintentar en el siguiente ciclo. Si para entonces ya es stale (bar[i+2]), `discard_stale_signals` la limpia automáticamente.

### Qué soluciona
- Evita perder trades por error transitorio de MT5 post-reconexión.
- Evita señales "fantasma" que en backtest no existirían (divergencia backtest/live).

---

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
