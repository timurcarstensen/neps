import csv

# import datetime
import json
import os
import pickle
import random
import time

import matplotlib.pyplot as plt
import numpy as np
import tabulate
import torch
from scipy import stats


def set_seed(seed):
    np.random.seed(seed)
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.enabled = True
        torch.backends.cudnn.deterministic = True
        torch.cuda.manual_seed_all(seed)


def get_seed_states():
    return {
        "random_state": random.getstate(),
        "np_seed_state": np.random.get_state(),
        "torch_seed_state": torch.random.get_rng_state(),
        "torch_cuda_seed_state": torch.cuda.get_rng_state_all(),
    }


def set_seed_states(seed_states):
    random.setstate(seed_states["random_state"])
    np.random.set_state(seed_states["np_seed_state"])
    torch.random.set_rng_state(seed_states["torch_seed_state"])
    torch.cuda.set_rng_state_all(seed_states["torch_cuda_seed_state"])


def _check_data(pred, target):
    if not isinstance(pred, np.ndarray):
        pred = np.array(pred).reshape(-1)
    if not isinstance(target, np.ndarray):
        target = np.array(target).reshape(-1)
    return pred, target


def spearman(pred, target) -> float:
    """Compute the spearman correlation coefficient between prediction and target"""
    pred, target = _check_data(pred, target)
    coef_val, _ = stats.spearmanr(pred, target)
    return coef_val


class Experimentator:
    def __init__(self, max_iters: int, seed: int = None) -> None:
        super().__init__()
        self.max_iters = max_iters
        self.iteration = 0

        self.seed = seed

    def reset(self, seed):
        self.seed = seed
        if self.seed is not None:
            set_seed(self.seed)
        self.iteration = 0

    def has_budget(self):
        return bool(self.iteration < self.max_iters)

    def next_iteration(self):
        self.iteration += 1


class StatisticsTracker:
    def __init__(
        self,
        args,
        save_path: str,
        log: bool = True,
        is_gbo: bool = False,
        minimize: bool = True,
    ):
        self.start_time = time.time()
        self.end_time = np.nan
        self.seed = np.nan

        self.incumbents_eval: list = []
        self.incumbent_values_eval: list = []
        self.last_func_evals: list = []
        self.last_func_tests: list = []
        self.incumbents_test: list = []
        self.incumbent_values_test: list = []
        self.cum_train_times: list = []
        self.opt_details: list = []
        self.y_evals = None
        self.theoretical_best = 1e10
        self.number = 0
        self.id = None

        self.log = log
        self.save_path = save_path
        self.is_gbo = is_gbo
        self.args = args
        self.minimize = minimize

        options = vars(args)

        if not os.path.exists(self.save_path):
            os.makedirs(self.save_path)
        with open(
            os.path.join(self.save_path, "args.json"), mode="w", encoding="UTF-8"
        ) as f:
            json.dump(options, f, indent=6)

    def reset(self, identifier=None):
        self.start_time = time.time()
        self.end_time = np.nan

        self.incumbents_eval = []
        self.incumbent_values_eval = []
        self.cum_train_times = []
        self.last_func_evals = []
        self.last_func_tests = []
        self.incumbents_test = []
        self.incumbent_values_test = []
        self.opt_details = []
        self.y_evals = None
        self.theoretical_best = 1e10
        self.number += 1
        if identifier is not None:
            self.id = identifier

    def set_number(self, new_number: int):
        self.number = new_number

    def calculate_incumbent(self, x: list, y):
        best_idx = np.argmin(y) if self.minimize else np.argmax(y)
        incumbent = x[best_idx]
        # incumbent_value = np.exp(-y[best_idx]).item() if self.log else -y[best_idx]
        incumbent_value = y[best_idx]
        return incumbent, incumbent_value

    @staticmethod
    def calculate_cum_train_time(train_details):
        # Compute the cumulative training time.
        try:
            cum_train_time = np.sum(train_details).item()
        except TypeError:
            cum_train_time = np.nan
        return cum_train_time

    def update(
        self,
        x,
        y_eval,
        y_eval_cur,
        train_details,
        y_test=None,
        y_test_cur=None,
        opt_details=None,
    ):
        # Calculate Incumbent
        self.y_evals = y_eval
        incumbent, incumbent_value = self.calculate_incumbent(x, y_eval)
        self.incumbents_eval.append(incumbent)
        self.incumbent_values_eval.append(incumbent_value)

        # self.last_func_evals.append(
        #     np.exp(-np.max(y_eval_cur)) if self.log else -np.max(y_eval_cur)
        # )
        self.last_func_evals.append(
            np.min(y_eval_cur) if self.minimize else np.max(y_eval_cur)
        )

        if y_test is not None:
            incumbent, incumbent_value = self.calculate_incumbent(x, y_test)
            self.incumbents_test.append(incumbent)
            self.incumbent_values_test.append(incumbent_value)
        if y_test_cur is not None:
            # self.last_func_tests.append(
            #     np.exp(-np.max(y_test_cur)) if self.log else -np.max(y_test_cur)
            # )
            self.last_func_tests.append(
                np.min(y_test_cur) if self.minimize else np.max(y_test_cur)
            )
        if opt_details is not None:
            self.opt_details.append(opt_details)
            if "pool_vals" in self.opt_details[-1].keys():
                pool_vals = [x[0] for x in self.opt_details[-1]["pool_vals"]]
                # best_pool_val = (
                #     np.exp(-np.max(pool_vals)) if self.log else -np.max(pool_vals)
                # )
                best_pool_val = np.min(pool_vals) if self.minimize else np.max(pool_vals)
                self.theoretical_best = min(self.theoretical_best, best_pool_val)

        self.end_time = time.time()
        cum_train_time = self.calculate_cum_train_time(train_details)
        self.cum_train_times.append(cum_train_time)

    def plot(self):
        pool_vals = [x[0] for x in self.opt_details[-1]["pool_vals"]]

        plt.subplot(221)
        # True validation error vs GP posterior
        plt.title("Val")
        plt.plot(pool_vals, pool_vals, ".")
        plt.errorbar(
            pool_vals,
            self.opt_details[-1]["pool_preds_mean"],
            fmt=".",
            yerr=np.sqrt(np.diag(self.opt_details[-1]["pool_preds_cov"])),
            capsize=2,
            color="b",
            alpha=0.2,
        )
        plt.grid(True)
        plt.subplot(222)
        # Acquisition function
        plt.title("Acquisition")
        plt.plot(pool_vals, self.opt_details[-1]["acq_vals"], "b+")
        # plt.xlim([2.5, None])
        plt.subplot(223)
        plt.title("Train")

        y1, y2 = (
            self.y_evals[: -self.args.batch_size],
            self.y_evals[-self.args.batch_size :],
        )
        plt.plot(self.y_evals, self.y_evals, ".")
        plt.plot(
            y1, self.opt_details[-1]["train_preds_mean"][: -self.args.batch_size], "b+"
        )
        plt.plot(
            y2, self.opt_details[-1]["train_preds_mean"][-self.args.batch_size :], "r+"
        )

        # if args.verbose:
        #    print('Spearman: ', spearman(self.opt_details[-1]["pool_vals"], self.opt_details[-1]["pool_preds_mean"]))
        plt.subplot(224)
        # Best metrics so far
        xaxis = np.arange(len(self.incumbent_values_test))
        plt.plot(
            xaxis, self.incumbent_values_test, "-.", c="C1", label="Best test so far"
        )
        plt.plot(
            xaxis,
            self.incumbent_values_eval,
            "-.",
            c="C2",
            label="Best validation so far",
        )
        plt.legend()
        plt.savefig(
            os.path.join(
                self.save_path,
                f"plot_{self.id if self.id is not None else self.number}.png",
            )
        )
        plt.close()

    def write_to_csv(self):
        # save human-readable results
        columns = [
            "Iteration",
            "Last func val",
            "Incumbent Value val",
            "Time",
            "Cum Train Time",
        ]
        values = [
            len(self.last_func_evals) - 1,
            self.last_func_evals[-1],
            self.incumbent_values_eval[-1],
            self.end_time - self.start_time,
            self.cum_train_times[-1],
        ]
        if self.is_gbo:
            columns.append("Pool regret")
            if (
                len(self.opt_details) > 0
                and "pool" in self.opt_details[-1].keys()
                and "pool_vals" in self.opt_details[-1].keys()
            ):
                zipped_ranked = list(
                    sorted(
                        zip(
                            self.opt_details[-1]["pool_vals"],
                            self.opt_details[-1]["pool"],
                        ),
                        key=lambda x: x[0],
                    )
                )[::-1]
                true_best_pool = (
                    np.exp(-zipped_ranked[0][0][0])
                    if self.log
                    else -zipped_ranked[0][0][0]
                )
                regret = np.abs(self.last_func_evals[-1] - true_best_pool)
                values.append(regret)
            else:
                values.append(None)

            columns.append("Theoretical Best")
            if len(self.opt_details) > 0 and "pool_vals" in self.opt_details[-1].keys():
                values.append(self.theoretical_best)
            else:
                values.append(None)

        columns.append("Last func test")
        if self.last_func_tests:
            values.append(self.last_func_tests[-1])
        else:
            values.append(None)

        columns.append("Incumbent Value test")
        if self.incumbent_values_test:
            values.append(self.incumbent_values_test[-1])
        else:
            values.append(None)

        full_path = os.path.join(
            self.save_path,
            f"results_{self.id if self.id is not None else self.number}.csv",
        )
        file_exists = os.path.isfile(full_path)
        with open(
            full_path, mode="a" if file_exists else "w", encoding="UTF-8"
        ) as csvfile:
            writer = csv.writer(csvfile, delimiter=",")
            if not file_exists:
                writer.writerow(columns)
            writer.writerow(values)

    def print(self, plot: bool = False, write_to_csv: bool = True):
        if plot and len(self.opt_details) > 0:
            self.plot()

        columns = [
            "Iteration",
            "Last func val",
            "Incumbent Value val",
            "Time",
            "Train Time",
        ]

        values = [
            str(len(self.last_func_evals) - 1),
            str(self.last_func_evals[-1]),
            str(self.incumbent_values_eval[-1]),
            str(self.end_time - self.start_time),
            str(self.cum_train_times[-1]),
        ]
        if (
            len(self.opt_details) > 0
            and "pool" in self.opt_details[-1].keys()
            and "pool_vals" in self.opt_details[-1].keys()
        ):
            columns.append("Pool regret")
            zipped_ranked = list(
                sorted(
                    zip(self.opt_details[-1]["pool_vals"], self.opt_details[-1]["pool"]),
                    key=lambda x: x[0],
                )
            )[::-1]
            true_best_pool = (
                np.exp(-zipped_ranked[0][0][0]) if self.log else -zipped_ranked[0][0][0]
            )
            regret = np.abs(self.last_func_evals[-1] - true_best_pool)
            values.extend([regret])
        if len(self.opt_details) > 0 and "pool_vals" in self.opt_details[-1].keys():
            columns.append("Theoretical Best")  # excluding initial design
            values.extend([self.theoretical_best])
        if self.last_func_tests:
            columns.append("Last func test")
            values.extend([self.last_func_tests[-1]])
        if self.incumbent_values_test:
            columns.append("Incumbent Value test")
            values.extend([self.incumbent_values_test[-1]])

        table = tabulate.tabulate(
            [values], headers=columns, tablefmt="simple", floatfmt="8.4f"
        )

        if len(self.last_func_evals) == 1 or len(self.last_func_evals) == 2:
            table = table.split("\n")
            table = "\n".join([table[1]] + table)
        else:
            table = table.split("\n")[2]
        print(table)

        if write_to_csv:
            self.write_to_csv()

        if not os.path.isdir(os.path.dirname(self.save_path)):
            os.makedirs(os.path.dirname(self.save_path))
            with open(
                os.path.join(self.save_path, "log.txt"), mode="w+", encoding="UTF-8"
            ) as o:
                o.write(table + "\n")
        else:
            with open(
                os.path.join(self.save_path, "log.txt"), mode="a+", encoding="UTF-8"
            ) as o:
                o.write(table + "\n")

    def save_results(self):
        # save all data for later use
        results = {
            "incumbents_eval": [inc for inc in self.incumbents_eval],
            "incumbent_value_eval": self.incumbent_values_eval,
            "runtime": self.cum_train_times,
            "last_func_evals": self.last_func_evals,
        }

        if self.incumbents_test:
            results["incumbent_test"] = ([inc for inc in self.incumbents_test],)
        if self.incumbent_values_test:
            results["incumbent_values_test"] = self.incumbent_values_test
        if self.last_func_tests:
            results["last_func_tests"] = self.last_func_tests
        if self.opt_details:
            results["opt_details"] = self.opt_details

        with open(
            os.path.join(self.save_path, "data.p"),
            "wb",
        ) as handle:
            pickle.dump(
                results,
                handle,
            )

    def read_checkpoint(self):
        checkpoint_path = os.path.join(
            self.save_path,
            f"checkpoint_{self.id if self.id is not None else self.number}.json",
        )
        if os.path.isfile(checkpoint_path):
            with open(checkpoint_path, encoding="UTF-8") as f:
                checkpoint_data = json.load(f)
        else:
            raise Exception(f"Checkpoint {checkpoint_path} does not exist!")
        return checkpoint_data

    def save_checkpoint(self, checkpoint_data: list):
        checkpoint_path = os.path.join(
            self.save_path,
            f"checkpoint_{self.id if self.id is not None else self.number}.json",
        )
        if not os.path.isdir(os.path.dirname(self.save_path)):
            os.makedirs(os.path.dirname(self.save_path))

        if os.path.isfile(checkpoint_path):
            _checkpoint_data = self.read_checkpoint()
            _checkpoint_data += checkpoint_data
        else:
            _checkpoint_data = checkpoint_data

        with open(checkpoint_path, mode="w+", encoding="UTF-8") as f:
            json.dump(_checkpoint_data, f, indent=4)

    def save_seed_states(self):
        seed_state_path = os.path.join(
            self.save_path,
            f"seed_state_{self.id if self.id is not None else self.number}.pickle",
        )
        with open(seed_state_path, "wb") as handle:
            pickle.dump(
                get_seed_states(),
                handle,
            )

    def read_seed_states(self):
        seed_state_path = os.path.join(
            self.save_path,
            f"seed_state_{self.id if self.id is not None else self.number}.pickle",
        )
        with open(seed_state_path, "rb") as handle:
            seed_states = pickle.load(handle)
        return seed_states

    @staticmethod
    def set_seed_states(seed_states):
        set_seed_states(seed_states)
