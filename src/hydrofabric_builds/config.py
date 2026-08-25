"""A pydantic basemodel for setting HFConfig defaults"""

from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, Field, model_validator
from pyprojroot import here

from hydrofabric_builds._version import __version__
from hydrofabric_builds.schemas.hydrofabric import (
    BuildHydrofabricConfig,
    DivideAttributesModelConfig,
    FlowpathAttributesModelConfig,
    FPCrosswalkConfig,
    GagesConfig,
    WaterbodiesConfig,
)


class TaskSelection(BaseModel):
    """Config class for selecting tasks to run"""

    build_hydrofabric: bool = Field(
        default=True, description="Decides if we want to run the hydrofabric build tasks"
    )

    divide_attributes: bool = Field(
        default=True, description="Decides if we want to run the divide attributes task"
    )

    flowpath_attributes: bool = Field(
        default=True, description="Decides if we want to run the flowpath attributes task"
    )

    waterbodies: bool = Field(default=True, description="Decides if we want to run the waterbodies task")

    gages: bool = Field(default=True, description="Decides if we want to run the gages task")

    fp_crosswalk: bool = Field(
        default=True, description="Decides if we want to run the flowpath crosswalk task"
    )

    hydrolocations: bool = Field(
        default=True, description="Decides if we want to run the hydrolocations task"
    )


class HFConfig(BaseModel):
    """A config validation class for default build settings"""

    output_dir: Path = Field(
        default=here() / "data/",
        description="The directory for output files to be saved from Hydrofabric builds",
    )

    output_name: Path = Field(default=f"nhf_{__version__}.gpkg", description="The output file name")

    output_file_path: Path = Field(
        default_factory=lambda data: data["output_dir"] / data["output_name"],
        description="The full output file path",
    )

    tasks: TaskSelection = Field(description="Which tasks to run")

    crs: str = Field(
        default="EPSG:5070",
        description="Coordinate Reference System for the hydrofabric builds. Defaults to Conus Albers",
    )

    build: BuildHydrofabricConfig = Field(
        description="Settings for hydrofabric build", default=BuildHydrofabricConfig()
    )

    divide_attributes: DivideAttributesModelConfig = Field(
        default=DivideAttributesModelConfig(),
        description="Settings for building divide attributes",
    )

    flowpath_attributes: FlowpathAttributesModelConfig = Field(
        description="Settings for building flowpath attributes",
        default=FlowpathAttributesModelConfig(),
    )

    waterbodies: WaterbodiesConfig = Field(
        default=WaterbodiesConfig(), description="Settings for building waterbodies"
    )

    gages: GagesConfig = Field(default=GagesConfig(), description="Settings for building gages")

    fp_crosswalk: FPCrosswalkConfig = Field(
        default=FPCrosswalkConfig(), description="Settings for building flowpath crosswalks"
    )

    @classmethod
    def from_yaml(cls, path: str | Path) -> Self:
        """An internal method to read a config from a YAML file

        Parameters
        ----------
        path : str | Path
            The path to the provided YAML file

        Returns
        -------
        HFConfig
            A configuration object validated
        """
        with open(path) as f:
            data = yaml.safe_load(f)

        # create the divide attributes list
        try:
            attributes = []
            for _, val in enumerate(data["divide_attributes"]["attributes"]):
                # if there is no data_dir specified for each attribute, append the model's main data dir
                try:
                    data["divide_attributes"]["attributes"][val]["data_dir"]
                except KeyError:
                    data["divide_attributes"]["attributes"][val]["data_dir"] = data["divide_attributes"][
                        "data_dir"
                    ]
                attributes.append(data["divide_attributes"]["attributes"][val])
            data["divide_attributes"]["attributes"] = attributes
            data["divide_attributes"] = DivideAttributesModelConfig.model_validate(data["divide_attributes"])
        except KeyError:
            pass

        return cls(**data)

    @model_validator(mode="after")
    def inject_hf_path(self: Any) -> Self:  # type: ignore[misc,type-var]
        """Inject the hydrofabric path into divide and flowpath attributes"""
        self.divide_attributes.hf_path = self.output_file_path
        self.flowpath_attributes.hf_path = self.output_file_path
        self.divide_attributes.crs = self.crs
        return self
