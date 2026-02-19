"""Robust loss helpers for multi-stage optimization."""

from __future__ import annotations

import numpy as np


def huber_loss(residuals: np.ndarray, delta: float = 30.0) -> float:
    """Return summed Huber loss for residuals in minutes."""
    if residuals.size == 0:
        return 0.0
    abs_r = np.abs(residuals)
    quadratic = 0.5 * residuals * residuals
    linear = delta * (abs_r - 0.5 * delta)
    return float(np.where(abs_r <= delta, quadratic, linear).sum())


def tukey_biweight_loss(residuals: np.ndarray, c: float = 120.0) -> float:
    """Return summed Tukey biweight loss for residuals in minutes."""
    if residuals.size == 0:
        return 0.0
    abs_r = np.abs(residuals)
    u = residuals / c
    inside = abs_r <= c
    rho_inside = (c * c / 6.0) * (1.0 - np.power(1.0 - u * u, 3.0))
    rho_outside = np.full_like(residuals, c * c / 6.0)
    return float(np.where(inside, rho_inside, rho_outside).sum())


def robust_loss(
    residuals: np.ndarray,
    method: str = "huber",
    huber_delta: float = 30.0,
    tukey_c: float = 120.0,
) -> float:
    """Switchable robust loss wrapper."""
    method_normalized = (method or "huber").strip().lower()
    if method_normalized == "huber":
        return huber_loss(residuals, delta=huber_delta)
    if method_normalized == "tukey":
        return tukey_biweight_loss(residuals, c=tukey_c)
    raise ValueError(f"Unsupported robust loss method: {method}")
