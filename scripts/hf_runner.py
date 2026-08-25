"""Local runner for building the NGWPC hydrofabric"""

import argparse
from collections.abc import Callable
from datetime import datetime
from typing import Any, Self

from pydantic import ValidationError

from hydrofabric_builds import HFConfig, TaskInstance
from hydrofabric_builds.logs import setup_logging
from hydrofabric_builds.pipeline.build_divide_attributes import build_divide_attributes
from hydrofabric_builds.pipeline.build_flowpath_attributes import build_flowpath_attributes
from hydrofabric_builds.pipeline.build_fp_crosswalk import build_fp_crosswalk
from hydrofabric_builds.pipeline.build_gages import build_gages
from hydrofabric_builds.pipeline.build_graph import build_graph
from hydrofabric_builds.pipeline.build_hydrolocations import build_hydrolocations
from hydrofabric_builds.pipeline.build_waterbodies import build_waterbodies
from hydrofabric_builds.pipeline.download import download_reference_data
from hydrofabric_builds.pipeline.processing import (
    map_build_hydrofabric,
    map_trace_and_aggregate,
    reduce_combine_base_hydrofabric,
)
from hydrofabric_builds.pipeline.trace_graph_attributes import trace_hydrofabric_attributes
from hydrofabric_builds.pipeline.write import write_base_hydrofabric

logger = setup_logging()


class LocalRunner:
    """Execute pipeline tasks locally with Airflow-like interface.

    Parameters
    ----------
    config : HFConfig
        Pipeline configuration containing build settings and parameters.
    run_id : str or None, default=None
        Unique identifier for this pipeline run. If None, generated from
        current timestamp in format 'YYYYMMDD_HHMMSS'.

    Attributes
    ----------
    config : HFConfig
        The pipeline configuration.
    run_id : str
        Unique identifier for this run.
    ti : TaskInstance
        TaskInstance for XCom operations.
    results : dict[str, dict[str, Any]]
        Execution results for each task, keyed by task_id.
    """

    def __init__(
        self,
        config: HFConfig,
        run_id: str | None = None,
    ) -> None:
        """Initialize the LocalRunner.

        Parameters
        ----------
        config : HFConfig
            Pipeline configuration.
        run_id : str or None, default=None
            Optional run identifier. Auto-generated if not provided.
        """
        self.config: HFConfig = config
        self.run_id: str = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.ti: TaskInstance = TaskInstance()
        self.results: dict[str, dict[str, Any]] = {}

    def cleanup(self) -> None:
        """Clean up resources"""
        logger.info("runner: Closing processes")

    def __enter__(self: Self) -> Self:
        """Context manager entry."""
        return self

    def __exit__(self: Self, *args: str, **kwargs: str) -> None:
        """Context manager exit - ensures cleanup."""
        self.cleanup()

    def run_task(
        self,
        task_id: str,
        python_callable: Callable[..., Any],
        op_kwargs: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a single task.

        Parameters
        ----------
        task_id : str
            Unique identifier for this task. Used in XCom keys and result tracking.
        python_callable : Callable[..., Any]
            The function to execute. Must accept **kwargs to receive context.
        op_kwargs : dict[str, Any] or None, default=None
            Additional keyword arguments to pass to the callable.

        Returns
        -------
        Any
            The return value from the callable.
        """
        logger.info(f"Running task: {task_id}")

        context: dict[str, Any] = {
            "ti": self.ti,
            "task_id": task_id,
            "run_id": self.run_id,
            "ds": datetime.now().strftime("%Y-%m-%d"),
            "execution_date": datetime.now(),
            "config": self.config,
        }

        kwargs = {**(op_kwargs or {}), **context}

        result = python_callable(**kwargs)

        for k, v in result.items():
            self.ti.xcom_push(f"{task_id}.{k}", v)
        self.results[task_id] = {"status": "success", "result": result}

        logger.info(f"✓ Task {task_id} completed")
        return result

    def get_result(self, task_id: str) -> dict[str, Any]:
        """Retrieve execution results for a specific task.

        Parameters
        ----------
        task_id : str
            The identifier of the task to get results for.

        Returns
        -------
        dict[str, Any] or None
            Dictionary containing 'status' and either 'result' (on success)
            or 'error' (on failure). Returns None if task_id not found.
        """
        result = self.results.get(task_id)
        if result is None:
            raise ValueError("Cannot find result from task")
        return result


def main() -> int:
    """Main entry point for the hydrofabric-build pipeline CLI.

    Returns
    -------
    int
        Exit code: 0 for success, 1 for failure.
    """
    parser = argparse.ArgumentParser(description="A local runner for hydrofabric data processing")
    parser.add_argument("--config", required=False, help="Config file")
    args = parser.parse_args()

    try:
        config = HFConfig.from_yaml(args.config)
    except ValidationError as e:
        print("Configuration validation failed:")
        for error in e.errors():
            print(f"  {error['loc']}: {error['msg']}")
        return 1
    except FileNotFoundError:
        logger.error(f"Config file not found: {args.config}")
        return 1
    except TypeError as e:
        logger.error("Config file not specified.")
        raise TypeError("Config file not specified.") from e

    with LocalRunner(config) as runner:
        if config.tasks.build_hydrofabric:
            runner.run_task(task_id="download", python_callable=download_reference_data, op_kwargs={})
            runner.run_task(task_id="build_graph", python_callable=build_graph, op_kwargs={})
            runner.run_task(task_id="map_flowpaths", python_callable=map_trace_and_aggregate, op_kwargs={})
            runner.run_task(task_id="map_build_base", python_callable=map_build_hydrofabric, op_kwargs={})
            runner.run_task(
                task_id="reduce_base", python_callable=reduce_combine_base_hydrofabric, op_kwargs={}
            )
            runner.run_task(
                task_id="trace_attributes", python_callable=trace_hydrofabric_attributes, op_kwargs={}
            )
            runner.run_task(task_id="write_base", python_callable=write_base_hydrofabric, op_kwargs={})

        if config.tasks.gages:
            runner.run_task("gages", python_callable=build_gages, op_kwargs={})

        if config.tasks.waterbodies:
            runner.run_task("waterbodies", python_callable=build_waterbodies, op_kwargs={})

        if config.tasks.hydrolocations:
            runner.run_task("hydrolocations", python_callable=build_hydrolocations, op_kwargs={})

        if config.tasks.divide_attributes:
            runner.run_task(
                task_id="divide_attributes", python_callable=build_divide_attributes, op_kwargs={}
            )
        if config.tasks.flowpath_attributes:
            runner.run_task(
                task_id="flowpath_attributes", python_callable=build_flowpath_attributes, op_kwargs={}
            )
        if config.tasks.fp_crosswalk:
            runner.run_task(task_id="fp_crosswalk", python_callable=build_fp_crosswalk, op_kwargs={})

        print("\n" + "=" * 60)
        print("Pipeline completed")
        print("=" * 60)
        for task_id, info in runner.results.items():
            status = "✓" if info["status"] == "success" else "✗"
            print(f"  {status} {task_id}: {info['status']}")
        print("=" * 60)

    return 0


if __name__ == "__main__":
    exit(main())
