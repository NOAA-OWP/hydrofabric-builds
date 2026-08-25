import os
from pathlib import Path
from typing import Any

import yaml
from pyarrow import fs
from pyiceberg.catalog import Catalog, load_catalog

from hydrofabric_builds.helpers.creds import load_creds


def load_pyiceberg_config(cwd: Path) -> dict[str, Any]:
    """Reads a .pyiceberg.yaml config file to memory

    Parameters
    ----------
    cwd : Path
        the path to the .pyiceberg.yaml file

    Returns
    -------
    dict[str, Any]
        The pyiceberg yaml file

    Raises
    ------
    FileNotFoundError
        Can't find the YAML file in the CWD
    yaml.YAMLError
        Error parsing the YAML file
    """
    try:
        with open(cwd / ".pyiceberg.yaml", encoding="utf-8") as file:
            data = yaml.safe_load(file)
            return data if data is not None else {}
    except FileNotFoundError as e:
        raise FileNotFoundError(f".pyiceberg YAML file not found in cwd: {cwd}") from e
    except yaml.YAMLError as e:
        raise yaml.YAMLError(f"Error parsing .pyiceberg YAML file: {e}") from e


def setup_glue_catalog() -> Catalog:
    """Setup credentials and load the glue catalog

    Returns
    -------
    Catalog
        PyIceberg catalog
    """
    load_creds(dir=Path.cwd())
    # pyiceberg_config = load_pyiceberg_config(Path.cwd())
    return load_catalog("glue", **{"type": "glue", "glue.region": "us-east-1"})


def setup_sql_catalog() -> Catalog:
    """Setup config and load a local catalog

    Returns
    -------
    Catalog
        PyIceberg catalog
    """
    pyiceberg_config = load_pyiceberg_config(Path.cwd())
    return load_catalog(
        name="sql",
        type=pyiceberg_config["catalog"]["sql"]["type"],
        uri=pyiceberg_config["catalog"]["sql"]["uri"],
        warehouse=pyiceberg_config["catalog"]["sql"]["warehouse"],
    )


def s3_fs(region: str = "us-east-1") -> fs.S3FileSystem:
    """Setup s3 file system with credentials for reading and writing parquet to s3

    Parameters
    ----------
    region : str, optional
        AWS region, by default "us-east-1"

    Returns
    -------
    fs.S3FileSystem
        PyArrow s3 File system
    """
    return fs.S3FileSystem(
        region=region,
        access_key=os.environ["AWS_ACCESS_KEY_ID"],
        secret_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        session_token=os.environ["AWS_SESSION_TOKEN"],
    )


def _resolve_templates(d: dict, env: dict) -> dict:
    """Expand {placeholders} in string leaves using env mapping. (gauges and reservoirs)"""

    def expand(val: dict) -> dict:
        """Expanding the vals in config"""
        if isinstance(val, str):
            try:
                return val.format(**env)
            except KeyError:
                return val
        if isinstance(val, dict):
            return {k: expand(v) for k, v in val.items()}
        if isinstance(val, list):
            return [expand(v) for v in val]
        return val

    return expand(d)


def load_config(path: Path) -> dict:
    """
    To load a YAML config (gauges and reservoirs)

    :param path: path to config file
    :return: nested dictionary format of config file
    """
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    env = {**cfg.get("roots", {})}
    env.update(os.environ)  # allow env-var overrides
    return _resolve_templates(cfg, env)
