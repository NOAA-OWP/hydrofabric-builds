import numpy as np
from numpy.typing import NDArray
from scipy.stats.mstats import gmean


def weighted_circular_mean(values: NDArray, coverage: NDArray) -> NDArray | float:
    """Circular mean aggregation function for exactextract

    Adapted from Astropy under BSD 3-Clause
    https://docs.astropy.org/en/stable/_modules/astropy/stats/circstats.html#circmean

    Parameters
    ----------
    values : NDArray
        Array in degrees
    coverage : NDArray
        Weights array

    Returns
    -------
    NDArray | float
        Array with circular mean on interval [-np.pi, np.pi) or NaN if inside of a lake
    """
    if isinstance(values, np.ma.MaskedArray):
        if values.count() == 0:
            return np.nan

    # Constants from astropy.circmean
    p = 1.0
    phi = 0.0
    axis = None

    values = np.radians(values)

    if coverage is None:
        weights = np.ones((1,))
    else:
        try:
            weights = np.broadcast_to(coverage, values.shape)
        except ValueError as e:
            raise ValueError("Weights and data have inconsistent shape.") from e

    C = np.sum(weights * np.cos(p * (values - phi)), axis) / np.sum(weights, axis)
    S = np.sum(weights * np.sin(p * (values - phi)), axis) / np.sum(weights, axis)

    # angle in the interval [-np.pi, np.pi)
    theta = np.arctan2(S, C)

    return theta


def weighted_geometric_mean(values: NDArray, coverage: NDArray) -> NDArray:
    """Weighted geometric mean function for exactextract

    Parameters
    ----------
    values : NDArray
        Values array
    coverage : NDArray
        Weights array

    Returns
    -------
    NDArray
        Array of geometric mean
    """
    return gmean(values, weights=coverage)
