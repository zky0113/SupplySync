"""Supply-chain forecasting, replenishment policies and simulation.

This package consumes the read-only data produced by ``generate_data.py`` and
implements the three competition tasks:

1. Forecast future demand at every tier node.
2. Compare a decentralised replenishment policy (S1) against a privacy-aware
   collaborative policy (S2) that only shares aggregated forecasts.
3. Run an N-day simulation and report cost, stockout rate, inventory turnover
   and the bullwhip coefficient for both policies across all scenarios.
"""

SCHEMA_VERSION = "1.0"
