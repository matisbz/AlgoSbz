# Marco Documental

Este directorio fija el circuito documental para auditoría técnica, generación de ideas y verificación de paridad dentro de la fábrica de cuentas fondeadas.

## Estructura

- `00_baseline_system_map.md`: mapa base del sistema real y de sus huecos de validación.
- `ideas/IDEA-TEMPLATE.md`: contrato para nuevas hipótesis y líneas de investigación.
- `audits/AUDIT-TEMPLATE.md`: contrato para revisar cambios de Claude o del sistema.
- `parity/PARITY-TEMPLATE.md`: contrato para revisar equivalencia backtest/live.

## Regla de evidencia

Cada documento nuevo debe incluir una sección `Evidencia usada`.

Cada afirmación relevante debe apoyarse en al menos una de estas fuentes:

- `Config`: archivo de configuración vivo.
- `Código`: módulo, función o script concreto.
- `Test`: test automatizado existente o añadido.
- `Resultado`: salida reproducible de un comando o script.
- `Hipótesis`: si no existe evidencia suficiente, debe quedar marcada explícitamente como hipótesis.

## Workflow operativo

1. Cualquier cambio relevante en código, config, deck, reglas de riesgo o validación genera una nota en `docs/audits/`.
2. Cualquier ejecución de parity o análisis de divergencias genera una nota en `docs/parity/`.
3. Cualquier propuesta de mejora o línea de investigación persistente se registra en `docs/ideas/`.
4. El mapa base solo se actualiza cuando cambia el comportamiento estructural del sistema o cambia el estándar de evidencia.

## Convención de nombres

- Ideas: `IDEA-YYYYMMDD-<slug>.md`
- Auditorías: `AUDIT-YYYYMMDD-<slug>.md`
- Parity: `PARITY-YYYYMMDD-<slug>.md`

## Comandos reproducibles

- `python -m pytest -q tests/test_live_runtime.py`
- `python -m pytest -q tests/test_live_parity.py`
- `python -X utf8 scripts/validate_live.py --offline`
- `python -X utf8 scripts/production_sim.py`

## Nota operativa

Usar `python -m pytest` y no `pytest` a secas. En el estado actual del repositorio, `pytest` sin `-m` falla en descubrimiento del paquete `algosbz`.
