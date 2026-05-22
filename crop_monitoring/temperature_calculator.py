"""
Land Surface Temperature from Sentinel-3 SLSTR S8 and S9 brightness temperatures.
LST in Celsius.
"""

from __future__ import annotations

import numpy as np

# Emissivity (user spec)
EPSILON = 0.97


def lst_kelvin(T8: np.ndarray, T9: np.ndarray, epsilon: float = EPSILON) -> np.ndarray:
    """
    LST_K = T8 + 1.06*dT + 0.46*(dT^2) + 54.3*(1 − ε)
    dT = T8 - T9
    """
    T8 = np.asarray(T8, dtype=np.float64)
    T9 = np.asarray(T9, dtype=np.float64)
    dT = T8 - T9
    lst_k = T8 + 1.06 * dT + 0.46 * (dT ** 2) + 54.3 * (1.0 - epsilon)
    return np.where(np.isfinite(T8) & np.isfinite(T9), lst_k, np.nan)


def lst_celsius(T8: np.ndarray, T9: np.ndarray, epsilon: float = EPSILON) -> np.ndarray:
    """LST in degrees Celsius: LST_K - 273.15."""
    lst_k = lst_kelvin(T8, T9, epsilon)
    return np.where(np.isfinite(lst_k), lst_k - 273.15, np.nan)
