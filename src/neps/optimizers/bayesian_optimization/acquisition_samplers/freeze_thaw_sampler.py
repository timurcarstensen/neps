# type: ignore
from __future__ import annotations

import warnings
from copy import deepcopy

import numpy as np
import pandas as pd

from ....search_spaces.search_space import SearchSpace
from ...multi_fidelity.utils import MFObservedData, continuous_to_tabular
from .base_acq_sampler import AcquisitionSampler

from timeit import default_timer as timer

class FreezeThawSampler(AcquisitionSampler):
    SAMPLES_TO_DRAW = 3  # number of random samples to draw at lowest fidelity

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.observations = None
        self.b_step = None
        self.n = None
        self.pipeline_space = None
        self.is_tabular = False
        self.time_data = {}

    
    def _sample_new(
        self, index_from: int, n: int = None, ignore_fidelity: bool = False
    ) -> pd.Series:
        
        n = n if n is not None else self.SAMPLES_TO_DRAW
        new_configs = [self.pipeline_space.sample(
            patience=self.patience, user_priors=False, ignore_fidelity=ignore_fidelity
        ) for _ in range(n)]

        # if self.tabular_space is not None:
        #     # This function have 3 possible return options:
        #     # 1. Tabular data is provided then, n configs are sampled from the table
        #     # 2. Tabular data is not provided and a list of configs is provided then, same list of configs is returned
        #     # 3. Tabular data is not provided and a single config is provided then, n configs will be sampled randomly
        #     new_configs=self.tabular_space.sample(index_from=index_from, config=new_configs, n=n)
        
        return pd.Series(
            new_configs, index=range(index_from, index_from + len(new_configs))
        )

    def _sample_new_unique(
        self, index_from: int, n: int = None, patience: int = 10, ignore_fidelity: bool=False        
    ) -> pd.Series:
        n = n if n is not None else self.SAMPLES_TO_DRAW
        assert (
            patience > 0 and n > 0
        ), "Patience and SAMPLES_TO_DRAW must be larger than 0"

        existing_configs = self.observations.all_configs_list()
        new_configs = []
        for _ in range(n):
            # Sample patience times for an unobserved configuration
            for _ in range(patience):
                _config = self.pipeline_space.sample(
                    patience=self.patience, user_priors=False, ignore_fidelity=ignore_fidelity
                )
                # # Convert continuous into tabular if the space is tabular
                # _config = continuous_to_tabular(_config, self.tabular_space)
                # Iterate over all observed configs
                for config in existing_configs:
                    if _config.is_equal_value(config, include_fidelity=not ignore_fidelity):
                        # if the sampled config already exists
                        # do the next iteration of patience
                        break
                else:
                    # If the new sample is not equal to any previous
                    # then it's a new config
                    new_config = _config
                    break
            else:
                # TODO: use logger.warn here instead (karibbov)
                warnings.warn(
                    f"Couldn't find an unobserved configuration in {patience} "
                    f"iterations. Using an observed config instead"
                )
                # patience budget exhausted use the last sampled config anyway
                new_config = _config

            # append the new config to the list
            new_configs.append(new_config)

        return pd.Series(
            new_configs, index=range(index_from, index_from + len(new_configs))
        )

    def sample(
            self, 
            acquisition_function=None, 
            n: int = None, 
            set_new_sample_fidelity: int | float=None
        ) -> list():
        """Samples a new set and returns the total set of observed + new configs."""
        partial_configs = self.observations.get_partial_configs_at_max_seen()
        start = timer()
        new_configs = self._sample_new(
            index_from=self.observations.next_config_id(), n=n, ignore_fidelity=False
        )

        if self.is_tabular:
            _n = n if n is not None else self.SAMPLES_TO_DRAW
            _partial_ids = set([conf["id"].value for conf in partial_configs])
            _all_ids = set(self.pipeline_space.custom_grid_table.index.values)
            _new_configs = np.random.choice(list(_all_ids - _partial_ids), size=_n, replace=False)
            new_configs = [self.pipeline_space.sample(
                patience=self.patience, user_priors=False, ignore_fidelity=False
            ) for _ in range(_n)]
            for i, config in enumerate(new_configs):
                config["id"].value = _new_configs[i]
                config.fidelity.value = self.pipeline_space.fidelity.lower
            new_configs = pd.Series(
                new_configs, 
                index=np.arange(len(partial_configs), len(partial_configs) + len(new_configs))
            )

        end = timer()
        new_configs_time = end - start
        start = timer()
        if set_new_sample_fidelity is not None:
            for config in new_configs:
                config.fidelity.value = set_new_sample_fidelity
        end = timer()
        set_init_fidels_time = end - start

        start = timer()
        # Deep copy configs for fidelity updates
        partial_configs_list = []
        index_list = []
        for idx, config in partial_configs.items():
            _config = deepcopy(config)
            partial_configs_list.append(_config)
            index_list.append(idx)

        # We build a new series of partial configs to avoid
        # incrementing fidelities multiple times due to pass-by-reference
        partial_configs = pd.Series(partial_configs_list, index=index_list)
        end = timer()
        copy_partials_time = end - start

        start = timer()
        # Set fidelity for new configs
        for _, config in new_configs.items():
            config.fidelity.value = config.fidelity.lower

        configs = pd.concat([partial_configs, new_configs])
        end = timer()
        update_and_combine_time = end - start

        self.time_data = {"sample_new_configs_time": new_configs_time,
                          "sample_set_initial_fidelities_time": set_init_fidels_time,
                          "sample_copy_partial_configs_time": copy_partials_time,
                          "sample_update_and_combine_time": update_and_combine_time}
        
        return configs

    def set_state(
        self,
        pipeline_space: SearchSpace,
        observations: MFObservedData,
        b_step: int,
        n: int = None,
    ):
        # overload to select incumbent differently through observations
        self.pipeline_space = pipeline_space
        self.observations = observations
        self.b_step = b_step
        self.n = n if n is not None else self.SAMPLES_TO_DRAW
        if hasattr(self.pipeline_space, "custom_grid_table") and self.pipeline_space.custom_grid_table is not None:
            self.is_tabular = True

