import logging
import time

import neps


def run_pipeline(working_directory, some_float, some_cat):
    start = time.time()
    if some_cat != "a":
        y = some_float
    else:
        y = -some_float
    end = time.time()
    return {
        "loss": y,
        "info_dict": {
            "test_score": y,
            "train_time": end - start,
        },
    }


# neps uses the default values and a confidence in this default value to construct a prior
# that speeds up the search
pipeline_space = dict(
    some_float=neps.FloatParameter(
        lower=0, upper=1, default=0.3, default_confidence="low"
    ),
    some_cat=neps.CategoricalParameter(
        choices=["a", "b", "c"], default="a", default_confidence="high"
    ),
)

logging.basicConfig(level=logging.INFO)
neps.run(
    run_pipeline=run_pipeline,
    pipeline_space=pipeline_space,
    working_directory="results/user_priors_example",
    max_evaluations_total=20,
)
previous_results, pending_configs = neps.status("results/user_priors_example")
