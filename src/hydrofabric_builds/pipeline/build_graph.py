"""Contains all code for building a graph based on flowpath ids"""

import logging
from typing import Any, cast

from hydrofabric_builds.config import HFConfig
from hydrofabric_builds.hydrofabric.graph import (
    _build_graph,
    _build_rustworkx_object,
    _detect_cycles,
    _find_outlets_by_hydroseq,
    _partition_all_outlet_subgraphs,
)
from hydrofabric_builds.task_instance import TaskInstance

logger = logging.getLogger(__name__)


def build_graph(**context: dict[str, Any]) -> dict[str, Any] | list[str]:
    """
    Builds a graph of all downstream to upstream connectivity

    Parameters
    ----------
    **context : dict
        Airflow-compatible context containing:
        - ti : TaskInstance for XCom operations
        - config : HFConfig with pipeline configuration
        - task_id : str identifier for this task
        - run_id : str identifier for this pipeline run
        - ds : str execution date
        - execution_date : datetime object

    Returns
    -------
    dict[str, dict[str, Any] | list[str]]
        - All outlets from the hydrofabric to trace upstream for connectivity
        - The upstream dictionary containing upstream and downstream connections
    """
    ti = cast(TaskInstance, context["ti"])
    cfg = cast(HFConfig, context["config"])
    reference_flowpaths = ti.xcom_pull(task_id="download", key="reference_flowpaths")
    reference_divides = ti.xcom_pull(task_id="download", key="reference_divides")

    logger.info("build_graph task: Constructing Network Graph")
    upstream_dict = _build_graph(reference_flowpaths)
    digraph, node_indices = _build_rustworkx_object(upstream_dict)
    _detect_cycles(digraph)

    logger.info("build_graph task: Finding all outlets")
    outlets = _find_outlets_by_hydroseq(reference_flowpaths)

    outlets_to_process = outlets[: cfg.build.debug_outlet_count] if cfg.build.debug_outlet_count else outlets
    logger.info("build_graph task: Partitioning Data via outlet")
    outlet_subgraphs = _partition_all_outlet_subgraphs(
        outlets_to_process,
        digraph,
        node_indices,
        reference_flowpaths,
        reference_divides,
    )

    return {
        "outlets": outlets,
        "digraph": digraph,
        "node_indices": node_indices,
        "outlet_subgraphs": outlet_subgraphs,
    }
