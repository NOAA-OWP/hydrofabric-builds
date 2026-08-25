import pickle
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from examples.divides_mapping import main  # adjust if your module name is different


@pytest.fixture
def tiny_config_and_data(tmp_path: Path):
    """Create tiny test GeoPackages/Shapefiles and a config dict."""
    # ---- make a tiny ref_fabric GeoDataFrame (g1) ----
    # p1: unit square from (0,0) to (1,1)
    # p2: another square far away (no overlap)
    g1 = gpd.GeoDataFrame(
        {
            "divide_id": ["p1", "p2"],
            "geometry": [
                Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
                Polygon([(10, 10), (11, 10), (11, 11), (10, 11)]),
            ],
        },
        crs="EPSG:4326",
    )

    ref_fabric_path = tmp_path / "ref_fabric.gpkg"
    # IMPORTANT: layer name must match the code: "reference_divides"
    g1.to_file(ref_fabric_path, layer="reference_divides", driver="GPKG")

    # ---- make a tiny MERIT GeoDataFrame (g2) ----
    # One polygon that overlaps *half* of p1:
    # overlap with p1 = a rectangle (0.5,0)-(1,1) => area = 0.5
    # p1 area = 1.0 => pct_overlap = 0.5
    g2 = gpd.GeoDataFrame(
        {
            "COMID": ["c1"],
            "geometry": [
                Polygon([(0.5, 0), (1.5, 0), (1.5, 1), (0.5, 1)]),
            ],
        },
        crs="EPSG:4326",
    )

    merit_path = tmp_path / "merit.shp"
    g2.to_file(merit_path)

    # ---- output directory ----
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    # ---- config dict matching the script structure ----
    cfg = {
        "paths": {
            "ref_fabric_path": str(ref_fabric_path),
            "merit_path": str(merit_path),
            "out_dir": str(out_dir),
        },
        "columns": {
            "name1": "divide_id",
            "name2": "COMID",
        },
        # use 4326 here so we don't actually reproject in the test
        "crs": {
            "equal_area": "EPSG:5070",
            "g2_assume": "EPSG:4326",
        },
        "chunking": {
            "chunk_size": 1,  # small to exercise the chunk logic
        },
        "checkpoint": {
            "checkpoint_name": "nested_checkpoint.pkl",
            "final_name": "nested_final.pkl",
        },
    }

    return cfg, out_dir


def test_main_builds_expected_nested_dict(tiny_config_and_data):
    cfg, out_dir = tiny_config_and_data

    # Run main function
    main(cfg)

    # Load the final pickle
    final_path = out_dir / cfg["checkpoint"]["final_name"]
    assert final_path.exists(), "Final nested dict pickle was not created"

    with final_path.open("rb") as f:
        nested = pickle.load(f)

    # We expect:
    # - only p1 has overlap (with c1)
    # - pct_of_p1 ~ 0.5 (half the area)
    assert "p1" in nested
    assert "p2" not in nested or nested["p2"] == {}

    inner = nested["p1"]
    assert "c1" in inner
    assert inner["c1"] == pytest.approx(0.5, rel=1e-1)
