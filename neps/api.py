"""API for the neps package.
"""
from __future__ import annotations

import inspect
import logging
import warnings
from pathlib import Path
from typing import Callable, Iterable, Literal

import ConfigSpace as CS
from neps.utils.run_args import check_essential_arguments, \
    get_run_args_from_yaml, \
    check_double_reference

from neps.utils.common import instance_from_map
from neps.runtime import launch_runtime
from neps.optimizers import BaseOptimizer, SearcherMapping
from neps.search_spaces.parameter import Parameter
from neps.search_spaces.search_space import (
    SearchSpace,
    pipeline_space_from_configspace,
    pipeline_space_from_yaml,
)
from neps.status.status import post_run_csv
from neps.utils.common import get_searcher_data, get_value
from neps.optimizers.info import SearcherConfigs


def run(
    run_pipeline: Callable | None = None,
    root_directory: str | Path | None = None,
    pipeline_space: (
        dict[str, Parameter | CS.ConfigurationSpace]
        | str
        | Path
        | CS.ConfigurationSpace
        | None
    ) = None,
    run_args: str | Path | None = None,
    overwrite_working_directory: bool = False,
    post_run_summary: bool = True,
    development_stage_id=None,
    task_id=None,
    max_evaluations_total: int | None = None,
    max_evaluations_per_run: int | None = None,
    continue_until_max_evaluation_completed: bool = False,
    max_cost_total: int | float | None = None,
    ignore_errors: bool = False,
    loss_value_on_error: None | float = None,
    cost_value_on_error: None | float = None,
    pre_load_hooks: Iterable | None = None,
    searcher: (
        Literal[
            "default",
            "bayesian_optimization",
            "random_search",
            "hyperband",
            "priorband",
            "mobster",
            "asha",
            "regularized_evolution",
        ]
        | BaseOptimizer | Path
    ) = "default",
    **searcher_kwargs,
) -> None:
    """Run a neural pipeline search.

    To parallelize:
        To run a neural pipeline search with multiple processes or machines,
        simply call run(.) multiple times (optionally on different machines). Make sure
        that root_directory points to the same folder on the same filesystem, otherwise,
        the multiple calls to run(.) will be independent.

    Args:
        run_pipeline: The objective function to minimize.
        pipeline_space: The search space to minimize over.
        root_directory: The directory to save progress to. This is also used to
            synchronize multiple calls to run(.) for parallelization.
        run_args: An option for providing the optimization settings e.g.
            max_evaluation_total in a YAML file.
        overwrite_working_directory: If true, delete the working directory at the start of
            the run. This is, e.g., useful when debugging a run_pipeline function.
        post_run_summary: If True, creates a csv file after each worker is done,
            holding summary information about the configs and results.
        development_stage_id: ID for the current development stage. Only needed if
            you work with multiple development stages.
        task_id: ID for the current task. Only needed if you work with multiple
            tasks.
        max_evaluations_total: Number of evaluations after which to terminate.
        max_evaluations_per_run: Number of evaluations the specific call to run(.) should
            maximally do.
        continue_until_max_evaluation_completed: If true, only stop after
            max_evaluations_total have been completed. This is only relevant in the
            parallel setting.
        max_cost_total: No new evaluations will start when this cost is exceeded. Requires
            returning a cost in the run_pipeline function, e.g.,
            `return dict(loss=loss, cost=cost)`.
        ignore_errors: Ignore hyperparameter settings that threw an error and do not raise
            an error. Error configs still count towards max_evaluations_total.
        loss_value_on_error: Setting this and cost_value_on_error to any float will
            supress any error and will use given loss value instead. default: None
        cost_value_on_error: Setting this and loss_value_on_error to any float will
            supress any error and will use given cost value instead. default: None
        pre_load_hooks: List of functions that will be called before load_results().
        searcher: Which optimizer to use. Can be a string identifier, an
            instance of BaseOptimizer, or a Path to a custom optimizer.
        **searcher_kwargs: Will be passed to the searcher. This is usually only needed by
            neps develolpers.

    Raises:
        ValueError: If deprecated argument working_directory is used.
        ValueError: If root_directory is None.


    Example:
        >>> import neps

        >>> def run_pipeline(some_parameter: float):
        >>>    validation_error = -some_parameter
        >>>    return validation_error

        >>> pipeline_space = dict(some_parameter=neps.FloatParameter(lower=0, upper=1))

        >>> logging.basicConfig(level=logging.INFO)
        >>> neps.run(
        >>>    run_pipeline=run_pipeline,
        >>>    pipeline_space=pipeline_space,
        >>>    root_directory="usage_example",
        >>>    max_evaluations_total=5,
        >>> )
    """
    if "working_directory" in searcher_kwargs:
        raise ValueError(
            "The argument 'working_directory' is deprecated, please use 'root_directory' "
            "instead"
        )

    if "budget" in searcher_kwargs:
        warnings.warn(
            "The argument: 'budget' is deprecated. In the neps.run call, please, use "
            "'max_cost_total' instead. In future versions using `budget` will fail.",
            DeprecationWarning,
            stacklevel=2,
        )
        max_cost_total = searcher_kwargs["budget"]
        del searcher_kwargs["budget"]
    logger = logging.getLogger("neps")

    if run_args:
        optim_settings = get_run_args_from_yaml(run_args)
        check_double_reference(run, locals(), optim_settings)
        run_pipeline = optim_settings.get("run_pipeline", run_pipeline)
        root_directory = optim_settings.get("root_directory", root_directory)
        pipeline_space = optim_settings.get("pipeline_space", pipeline_space)
        overwrite_working_directory = optim_settings.get(
            "overwrite_working_directory", overwrite_working_directory
        )
        post_run_summary = optim_settings.get("post_run_summary", post_run_summary)
        development_stage_id = optim_settings.get("development_stage_id",
                                                  development_stage_id)
        task_id = optim_settings.get("task_id", task_id)
        max_evaluations_total = optim_settings.get("max_evaluations_total",
                                                   max_evaluations_total)
        max_evaluations_per_run = optim_settings.get("max_evaluations_per_run",
                                                     max_evaluations_per_run)
        continue_until_max_evaluation_completed = optim_settings.get(
            "continue_until_max_evaluation_completed",
            continue_until_max_evaluation_completed)
        max_cost_total = optim_settings.get("max_cost_total", max_cost_total)
        ignore_errors = optim_settings.get("ignore_errors", ignore_errors)
        loss_value_on_error = optim_settings.get("loss_value_on_error",
                                                 loss_value_on_error)
        cost_value_on_error = optim_settings.get("cost_value_on_error",
                                                 cost_value_on_error)
        pre_load_hooks = optim_settings.get("pre_load_hooks", pre_load_hooks)
        searcher = optim_settings.get("searcher", searcher)
        # considers arguments of a provided SubClass of BaseOptimizer
        searcher_class_arguments = optim_settings.get("custom_class_searcher_kwargs", {})

    # check if necessary arguments are provided.
    check_essential_arguments(
        run_pipeline,
        root_directory,
        pipeline_space,
        max_cost_total,
        max_evaluations_total,
        searcher,
        run_args,
    )

    if pre_load_hooks is None:
        pre_load_hooks = []

    logger.info(f"Starting neps.run using root directory {root_directory}")

    # Used to create the yaml holding information about the searcher.
    # Also important for testing and debugging the api.
    searcher_info = {
        "searcher_name": "",
        "searcher_alg": "",
        "searcher_selection": "",
        "neps_decision_tree": True,
        "searcher_args": {},
    }

    # special case if you load your own optimizer via run_args
    if inspect.isclass(searcher):
        if issubclass(searcher, BaseOptimizer):
            search_space = SearchSpace(**pipeline_space)
            # aligns with the behavior of the internal neps searcher which also overwrites
            # its arguments by using searcher_kwargs
            merge_kwargs = {**searcher_class_arguments, **searcher_kwargs}
            searcher_info["searcher_args"] = merge_kwargs
            searcher = searcher(search_space, **merge_kwargs)
        else:
            # Raise an error if searcher is not a subclass of BaseOptimizer
            raise TypeError(
                "The provided searcher must be a class that inherits from BaseOptimizer."
            )

    if isinstance(searcher, BaseOptimizer):
        searcher_instance = searcher
        searcher_info["searcher_name"] = "baseoptimizer"
        searcher_info["searcher_alg"] = searcher.whoami()
        searcher_info["searcher_selection"] = "user-instantiation"
        searcher_info["neps_decision_tree"] = False
    else:
        (
            searcher_instance,
            searcher_info,
        ) = _run_args(
            searcher_info=searcher_info,
            pipeline_space=pipeline_space,
            max_cost_total=max_cost_total,
            ignore_errors=ignore_errors,
            loss_value_on_error=loss_value_on_error,
            cost_value_on_error=cost_value_on_error,
            logger=logger,
            searcher=searcher,
            **searcher_kwargs,
        )

    # Check to verify if the target directory contains history of another optimizer state
    # This check is performed only when the `searcher` is built during the run
    if not isinstance(searcher, (BaseOptimizer, str, dict, Path)):
        raise ValueError(
            f"Unrecognized `searcher` of type {type(searcher)}. Not str or BaseOptimizer."
        )
    elif isinstance(searcher, BaseOptimizer):
        # This check is not strict when a user-defined neps.optimizer is provided
        logger.warning(
            "An instantiated optimizer is provided. The safety checks of NePS will be "
            "skipped. Accurate continuation of runs can no longer be guaranteed!"
        )

    if task_id is not None:
        root_directory = Path(root_directory) / f"task_{task_id}"
    if development_stage_id is not None:
        root_directory = Path(root_directory) / f"dev_{development_stage_id}"

    launch_runtime(
        evaluation_fn=run_pipeline,
        sampler=searcher_instance,
        optimizer_info=searcher_info,
        optimization_dir=root_directory,
        max_evaluations_total=max_evaluations_total,
        max_evaluations_per_run=max_evaluations_per_run,
        continue_until_max_evaluation_completed=continue_until_max_evaluation_completed,
        logger=logger,
        loss_value_on_error=loss_value_on_error,
        ignore_errors=ignore_errors,
        overwrite_optimization_dir=overwrite_working_directory,
        pre_load_hooks=pre_load_hooks,
    )

    if post_run_summary:
        assert root_directory is not None
        post_run_csv(root_directory)


def _run_args(
    searcher_info: dict,
    pipeline_space: (
        dict[str, Parameter | CS.ConfigurationSpace]
        | str
        | Path
        | CS.ConfigurationSpace
        | None
    ) = None,
    max_cost_total: int | float | None = None,
    ignore_errors: bool = False,
    loss_value_on_error: None | float = None,
    cost_value_on_error: None | float = None,
    logger=None,
    searcher: (
        Literal[
            "default",
            "bayesian_optimization",
            "random_search",
            "hyperband",
            "priorband",
            "mobster",
            "asha",
            "regularized_evolution",
        ]
        | BaseOptimizer
    ) = "default",
    **searcher_kwargs,
) -> tuple[BaseOptimizer, dict]:
    try:
        # Raising an issue if pipeline_space is None
        if pipeline_space is None:
            raise ValueError(
                "The choice of searcher requires a pipeline space to be provided"
            )
        # Support pipeline space as ConfigurationSpace definition
        if isinstance(pipeline_space, CS.ConfigurationSpace):
            pipeline_space = pipeline_space_from_configspace(pipeline_space)
        # Support pipeline space as YAML file
        elif isinstance(pipeline_space, (str, Path)):
            pipeline_space = pipeline_space_from_yaml(pipeline_space)

        # Support pipeline space as mix of ConfigurationSpace and neps parameters
        new_pipeline_space: dict[str, Parameter] = dict()
        for key, value in pipeline_space.items():
            if isinstance(value, CS.ConfigurationSpace):
                config_space_parameters = pipeline_space_from_configspace(value)
                new_pipeline_space = {**new_pipeline_space, **config_space_parameters}
            else:
                new_pipeline_space[key] = value
        pipeline_space = new_pipeline_space

        # Transform to neps internal representation of the pipeline space
        pipeline_space = SearchSpace(**pipeline_space)
    except TypeError as e:
        message = f"The pipeline_space has invalid type: {type(pipeline_space)}"
        raise TypeError(message) from e

    # Load the information of the optimizer
    if isinstance(searcher, (str, Path)) and searcher not in \
        SearcherConfigs.get_searchers() and searcher != "default":
        # The users have their own custom searcher provided via yaml.
        logging.info("Preparing to run user created searcher")

        searcher_config, file_name = get_searcher_data(searcher,
                                                      loading_custom_searcher=True)
        # name defined via key or the filename of the yaml
        searcher_name = searcher_config.pop("name", file_name)
        searcher_info["searcher_selection"] = "user-yaml"
        searcher_info["neps_decision_tree"] = False
    elif isinstance(searcher, dict):
        custom_config = searcher
        default_config, searcher_name = get_searcher_data(searcher["strategy"])
        searcher_config = {**default_config, **custom_config}
        if "name" not in searcher_config:
            searcher_name = "custom_" + searcher_name
        else:
            searcher_name = searcher_config.pop("name")
        searcher_info["searcher_selection"] = "user-run_args-yaml"
        searcher_info["neps_decision_tree"] = False
    else:
        if searcher in ["default", None]:
            # NePS decides the searcher according to the pipeline space.
            if pipeline_space.has_prior:
                searcher = "priorband" if pipeline_space.has_fidelity else "pibo"
            else:
                searcher = (
                    "hyperband"
                    if pipeline_space.has_fidelity
                    else "bayesian_optimization"
                )
            searcher_info["searcher_selection"] = "neps-default"
        else:
            # Users choose one of NePS searchers.
            searcher_info["neps_decision_tree"] = False
            searcher_info["searcher_selection"] = "neps-default"
        # Fetching the searcher data, throws an error when the searcher is not found
        searcher_config, searcher_name = get_searcher_data(searcher)

    # Check for deprecated 'algorithm' argument
    if "algorithm" in searcher_config:
        warnings.warn(
            "The 'algorithm' argument is deprecated and will be removed in "
            "future versions. Please use 'strategy' instead.",
            DeprecationWarning
        )
        # Map the old 'algorithm' argument to 'strategy'
        searcher_config['strategy'] = searcher_config.pop("algorithm")

    if "strategy" in searcher_config:
        searcher_alg = searcher_config.pop("strategy")
    else:
        raise KeyError(f"Missing key strategy in searcher config:{searcher_config}")


    logger.info(f"Running {searcher_name} as the searcher")
    logger.info(f"Strategy: {searcher_alg}")


    # Used to create the yaml holding information about the searcher.
    # Also important for testing and debugging the api.
    searcher_info["searcher_name"] = searcher_name
    searcher_info["searcher_alg"] = searcher_alg

    # Updating searcher arguments from searcher_kwargs
    for key, value in searcher_kwargs.items():
        if not searcher_info["neps_decision_tree"]:
            if key not in searcher_config or searcher_config[key] != value:
                searcher_config[key] = value
                logger.info(
                    f"Updating the current searcher argument '{key}'"
                    f" with the value '{get_value(value)}'"
                )
            else:
                logger.info(
                    f"The searcher argument '{key}' has the same"
                    f" value '{get_value(value)}' as default."
                )
        else:
            # No searcher argument updates when NePS decides the searcher.
            logger.info(35 * "=" + "WARNING" + 35 * "=")
            logger.info("CHANGINE ARGUMENTS ONLY WORKS WHEN SEARCHER IS DEFINED")
            logger.info(
                f"The searcher argument '{key}' will not change to '{value}'"
                f" because NePS chose the searcher"
            )

    searcher_info["searcher_args"] = get_value(searcher_config)

    searcher_config.update(
        {
            "loss_value_on_error": loss_value_on_error,
            "cost_value_on_error": cost_value_on_error,
            "ignore_errors": ignore_errors,
        }
    )

    searcher_instance = instance_from_map(
        SearcherMapping, searcher_alg, "searcher", as_class=True
    )(
        pipeline_space=pipeline_space,
        budget=max_cost_total,  # TODO: use max_cost_total everywhere
        **searcher_config,
    )

    return (
        searcher_instance,
        searcher_info,
    )
