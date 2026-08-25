"""Tests for any reduction tasks for taking parallel workflows and unifying outputs"""

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, Polygon

from hydrofabric_builds.pipeline.processing import _combine_hydrofabrics


@pytest.fixture
def sample_hydrofabric_outlet1() -> dict[str, (gpd.GeoDataFrame | pd.DataFrame)]:
    """Sample hydrofabric for outlet 1."""
    crs = "EPSG:5070"
    return {
        "flowpaths": gpd.GeoDataFrame(
            {
                "fp_id": [1, 2],
                "dn_nex_id": [1, 2],
                "up_nex_id": [None, 1],
                "div_id": [1, 2],
                "geometry": [
                    LineString([(0, 0), (1, 1)]),
                    LineString([(1, 1), (2, 2)]),
                ],
            },
            crs=crs,
        ),
        "divides": gpd.GeoDataFrame(
            {
                "div_id": [1, 2],
                "type": ["aggregate", "independent"],
                "ref_ids": [["fp1"], ["fp2"]],
                "geometry": [
                    Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
                    Polygon([(1, 1), (2, 1), (2, 2), (1, 2)]),
                ],
            },
            crs=crs,
        ),
        "nexus": gpd.GeoDataFrame(
            {
                "nex_id": [1, 2],
                "dn_fp_id": [2, None],
                "geometry": [Point(1, 1), Point(2, 2)],
            },
            crs=crs,
        ),
        "reference_flowpaths": pd.DataFrame(
            {
                "ref_fp_id": [1, 2],
                "fp_id": [1, 2],
            }
        ),
    }


@pytest.fixture
def sample_hydrofabric_outlet2() -> dict[str, (gpd.GeoDataFrame | pd.DataFrame)]:
    """Sample hydrofabric for outlet 2."""
    crs = "EPSG:5070"
    return {
        "flowpaths": gpd.GeoDataFrame(
            {
                "fp_id": [3, 4, 5],
                "dn_nex_id": [3, 4, 5],
                "up_nex_id": [None, None, 3],
                "div_id": [3, 4, 5],
                "geometry": [
                    LineString([(3, 3), (4, 4)]),
                    LineString([(4, 4), (5, 5)]),
                    LineString([(5, 5), (6, 6)]),
                ],
            },
            crs=crs,
        ),
        "divides": gpd.GeoDataFrame(
            {
                "div_id": [3, 4, 5],
                "type": ["aggregate", "connector", "independent"],
                "ref_ids": [["fp3"], ["fp4"], ["fp5"]],
                "geometry": [
                    Polygon([(3, 3), (4, 3), (4, 4), (3, 4)]),
                    Polygon([(4, 4), (5, 4), (5, 5), (4, 5)]),
                    Polygon([(5, 5), (6, 5), (6, 6), (5, 6)]),
                ],
            },
            crs=crs,
        ),
        "nexus": gpd.GeoDataFrame(
            {
                "nex_id": [3, 4, 5],
                "dn_fp_id": [4, 5, None],
                "geometry": [Point(4, 4), Point(5, 5), Point(6, 6)],
            },
            crs=crs,
        ),
        "reference_flowpaths": pd.DataFrame(
            {
                "ref_fp_id": [3, 4, 5],
                "fp_id": [3, 4, 5],
            }
        ),
    }


@pytest.fixture
def built_hydrofabrics(
    sample_hydrofabric_outlet1: dict, sample_hydrofabric_outlet2: dict
) -> dict[str, dict[str, gpd.GeoDataFrame]]:
    """Built hydrofabrics for multiple outlets."""
    return {
        "6720797": sample_hydrofabric_outlet1,
        "6720703": sample_hydrofabric_outlet2,
    }


class TestCombineHydrofabricsPure:
    """Tests for pure _combine_hydrofabrics function"""

    def test_combines_all_layers(self, built_hydrofabrics: dict) -> None:
        """Test that all layers are combined correctly."""
        result = _combine_hydrofabrics(built_hydrofabrics, "EPSG:5070")

        assert "flowpaths" in result
        assert "divides" in result
        assert "nexus" in result

        assert isinstance(result["flowpaths"], gpd.GeoDataFrame)
        assert isinstance(result["divides"], gpd.GeoDataFrame)
        assert isinstance(result["nexus"], gpd.GeoDataFrame)

    def test_concatenates_correct_number_of_features(self, built_hydrofabrics: dict) -> None:
        """Test that all features are included in combined output."""
        result = _combine_hydrofabrics(built_hydrofabrics, "EPSG:5070")

        # 2 flowpaths from outlet1 + 3 from outlet2 = 5 total
        assert len(result["flowpaths"]) == 5
        assert len(result["divides"]) == 5
        assert len(result["nexus"]) == 5

    def test_preserves_unique_ids(self, built_hydrofabrics: dict) -> None:
        """Test that IDs remain unique after combination."""
        result = _combine_hydrofabrics(built_hydrofabrics, "EPSG:5070")

        fp_ids = result["flowpaths"]["fp_id"].tolist()
        div_ids = result["divides"]["div_id"].tolist()
        nex_ids = result["nexus"]["nex_id"].tolist()

        # All IDs should be unique
        assert len(fp_ids) == len(set(fp_ids))
        assert len(div_ids) == len(set(div_ids))
        assert len(nex_ids) == len(set(nex_ids))

        # IDs should be 1-5
        assert sorted(fp_ids) == [1, 2, 3, 4, 5]
        assert sorted(div_ids) == [1, 2, 3, 4, 5]
        assert sorted(nex_ids) == [1, 2, 3, 4, 5]

    def test_preserves_crs(self, built_hydrofabrics: dict) -> None:
        """Test that CRS is preserved in combined output."""
        crs = "EPSG:5070"
        result = _combine_hydrofabrics(built_hydrofabrics, crs)

        assert result["flowpaths"].crs == crs
        assert result["divides"].crs == crs
        assert result["nexus"].crs == crs

    def test_raises_when_empty_dict(self) -> None:
        """Test error when empty hydrofabrics dict provided."""
        with pytest.raises(ValueError, match="No built hydrofabrics provided"):
            _combine_hydrofabrics({}, "EPSG:5070")

    def test_raises_when_missing_flowpaths_key(self, sample_hydrofabric_outlet1: dict) -> None:
        """Test error when flowpaths key missing from hydrofabric."""
        bad_hydrofabric = {
            "outlet1": {
                "divides": sample_hydrofabric_outlet1["divides"],
                "nexus": sample_hydrofabric_outlet1["nexus"],
                # Missing "flowpaths"
            }
        }

        with pytest.raises(KeyError, match="Missing 'flowpaths' for outlet outlet1"):
            _combine_hydrofabrics(bad_hydrofabric, "EPSG:5070")

    def test_raises_when_missing_divides_key(self, sample_hydrofabric_outlet1: dict) -> None:
        """Test error when divides key missing from hydrofabric."""
        bad_hydrofabric = {
            "outlet1": {
                "flowpaths": sample_hydrofabric_outlet1["flowpaths"],
                "nexus": sample_hydrofabric_outlet1["nexus"],
                # Missing "divides"
            }
        }

        with pytest.raises(KeyError, match="Missing 'divides' for outlet outlet1"):
            _combine_hydrofabrics(bad_hydrofabric, "EPSG:5070")

    def test_raises_when_missing_nexus_key(self, sample_hydrofabric_outlet1: dict) -> None:
        """Test error when nexus key missing from hydrofabric."""
        bad_hydrofabric = {
            "outlet1": {
                "flowpaths": sample_hydrofabric_outlet1["flowpaths"],
                "divides": sample_hydrofabric_outlet1["divides"],
                # Missing "nexus"
            }
        }

        with pytest.raises(KeyError, match="Missing 'nexus' for outlet outlet1"):
            _combine_hydrofabrics(bad_hydrofabric, "EPSG:5070")

    def test_handles_single_outlet(self, sample_hydrofabric_outlet1: dict) -> None:
        """Test combination with single outlet."""
        single_outlet = {"outlet1": sample_hydrofabric_outlet1}

        result = _combine_hydrofabrics(single_outlet, "EPSG:5070")

        assert len(result["flowpaths"]) == 2
        assert len(result["divides"]) == 2
        assert len(result["nexus"]) == 2

    def test_preserves_all_columns(self, built_hydrofabrics: dict) -> None:
        """Test that all columns are preserved in combined output."""
        result = _combine_hydrofabrics(built_hydrofabrics, "EPSG:5070")

        # Check flowpaths columns
        assert "fp_id" in result["flowpaths"].columns
        assert "dn_nex_id" in result["flowpaths"].columns
        assert "up_nex_id" in result["flowpaths"].columns
        assert "div_id" in result["flowpaths"].columns
        assert "geometry" in result["flowpaths"].columns

        # Check divides columns
        assert "div_id" in result["divides"].columns
        assert "type" in result["divides"].columns
        assert "ref_ids" in result["divides"].columns
        assert "geometry" in result["divides"].columns

        # Check nexus columns
        assert "nex_id" in result["nexus"].columns
        assert "dn_fp_id" in result["nexus"].columns
        assert "geometry" in result["nexus"].columns

    def test_preserves_data_types(self, built_hydrofabrics: dict) -> None:
        """Test that data types are preserved after combination."""
        result = _combine_hydrofabrics(built_hydrofabrics, "EPSG:5070")

        # Check types preserved
        assert pd.api.types.is_integer_dtype(result["flowpaths"]["fp_id"])
        assert pd.api.types.is_integer_dtype(result["divides"]["div_id"])
        assert pd.api.types.is_integer_dtype(result["nexus"]["nex_id"])

    def test_handles_different_crs(self, built_hydrofabrics: dict) -> None:
        """Test that output CRS can be different from input."""
        # Input is EPSG:5070, output requested as EPSG:4326
        result = _combine_hydrofabrics(built_hydrofabrics, "EPSG:4326")

        # Should have the requested CRS
        assert result["flowpaths"].crs == "EPSG:4326"
        assert result["divides"].crs == "EPSG:4326"
        assert result["nexus"].crs == "EPSG:4326"

    def test_resets_index(self, built_hydrofabrics: dict) -> None:
        """Test that indices are reset after concatenation."""
        result = _combine_hydrofabrics(built_hydrofabrics, "EPSG:5070")

        # After concat with ignore_index=True, indices should be 0-4
        assert list(result["flowpaths"].index) == [0, 1, 2, 3, 4]
        assert list(result["divides"].index) == [0, 1, 2, 3, 4]
        assert list(result["nexus"].index) == [0, 1, 2, 3, 4]

    def test_preserves_geometry_types(self, built_hydrofabrics: dict) -> None:
        """Test that geometry types are correct after combination."""
        result = _combine_hydrofabrics(built_hydrofabrics, "EPSG:5070")

        # Check geometry types
        assert all(geom.geom_type == "LineString" for geom in result["flowpaths"].geometry)
        assert all(geom.geom_type == "Polygon" for geom in result["divides"].geometry)
        assert all(geom.geom_type == "Point" for geom in result["nexus"].geometry)

    def test_handles_many_outlets(self) -> None:
        """Test combination with many outlets."""
        crs = "EPSG:5070"
        many_outlets = {}

        for i in range(10):
            many_outlets[f"outlet{i}"] = {
                "flowpaths": gpd.GeoDataFrame(
                    {
                        "fp_id": [i * 10 + j for j in range(5)],
                        "dn_nex_id": [i * 10 + j for j in range(5)],
                        "div_id": [i * 10 + j for j in range(5)],
                        "geometry": [LineString([(j, j), (j + 1, j + 1)]) for j in range(5)],
                    },
                    crs=crs,
                ),
                "divides": gpd.GeoDataFrame(
                    {
                        "div_id": [i * 10 + j for j in range(5)],
                        "type": ["aggregate"] * 5,
                        "geometry": [
                            Polygon([(j, j), (j + 1, j), (j + 1, j + 1), (j, j + 1)]) for j in range(5)
                        ],
                    },
                    crs=crs,
                ),
                "nexus": gpd.GeoDataFrame(
                    {
                        "nex_id": [i * 10 + j for j in range(5)],
                        "dn_fp_id": [None] * 5,
                        "geometry": [Point(j, j) for j in range(5)],
                    },
                    crs=crs,
                ),
                "reference_flowpaths": pd.DataFrame(
                    {
                        "ref_fp_id": [i * 10 + j for j in range(5)],
                        "fp_id": [i * 10 + j for j in range(5)],
                    }
                ),
            }

        result = _combine_hydrofabrics(many_outlets, crs)

        assert len(result["flowpaths"]) == 50
        assert len(result["divides"]) == 50
        assert len(result["nexus"]) == 50
        assert len(result["reference_flowpaths"]) == 50

    def test_empty_geodataframes(self) -> None:
        """Test handling of outlets with empty GeoDataFrames."""
        crs = "EPSG:5070"
        empty_outlet = {
            "outlet1": {
                "flowpaths": gpd.GeoDataFrame(
                    {"fp_id": [], "dn_nex_id": [], "div_id": [], "geometry": []}, crs=crs
                ),
                "divides": gpd.GeoDataFrame({"div_id": [], "type": [], "geometry": []}, crs=crs),
                "nexus": gpd.GeoDataFrame({"nex_id": [], "dn_fp_id": [], "geometry": []}, crs=crs),
                "reference_flowpaths": pd.DataFrame({"ref_fp_id": [], "fp_id": []}),
            }
        }

        with pytest.raises(ValueError):
            _ = _combine_hydrofabrics(empty_outlet, crs)
