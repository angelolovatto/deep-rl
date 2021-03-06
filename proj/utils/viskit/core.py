"""
This project was developed by Rocky Duan, Peter Chen, Pieter Abbeel for the
Berkeley Deep RL Bootcamp, August 2017. Bootcamp website with slides and lecture
 videos: https://sites.google.com/view/deep-rl-bootcamp/.

Copyright 2017 Deep RL Bootcamp Organizers.

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""
import itertools
import pandas
import json
import os


class AttrDict(dict):
    def __init__(self, *args, **kwargs):
        super(AttrDict, self).__init__(*args, **kwargs)
        self.__dict__ = self


def unique(l):
    return list(set(l))


def flatten(l):
    return [item for sublist in l for item in sublist]


def load_progress(progress_path, verbose=True):
    if verbose:
        print("Reading %s" % progress_path)

    if progress_path.endswith(".csv"):
        return pandas.read_csv(progress_path, index_col=None, comment="#")

    ds = []
    with open(progress_path, "rt") as fh:
        for line in fh:
            ds.append(json.loads(line))
    return pandas.DataFrame(ds)


def flatten_dict(d):
    flat_params = dict()
    for k, v in d.items():
        if isinstance(v, dict):
            v = flatten_dict(v)
            for subk, subv in flatten_dict(v).items():
                flat_params[k + "." + subk] = subv
        else:
            flat_params[k] = v
    return flat_params


def load_params(params_json_path):
    with open(params_json_path, "r") as f:
        data = json.loads(f.read())
        if "args_data" in data:
            del data["args_data"]
        if "exp_name" not in data:
            data["exp_name"] = params_json_path.split("/")[-3]
    return data


def lookup(d, keys):
    if not isinstance(keys, list):
        keys = keys.split(".")
    for k in keys:
        if hasattr(d, "__getitem__"):
            if k in d:
                d = d[k]
            else:
                return None
        else:
            return None
    return d


def load_exps_data(exp_folder_paths, ignore_missing_keys=False, verbose=True):
    if isinstance(exp_folder_paths, str):
        exp_folder_paths = [exp_folder_paths]
    exps = []
    for exp_folder_path in exp_folder_paths:
        exps += [x[0] for x in os.walk(exp_folder_path)]
    if verbose:
        print("finished walking exp folders")
    exps_data = []
    for exp in exps:
        try:
            exp_path = exp
            variant_json_path = os.path.join(exp_path, "variant.json")
            progress_csv_path = os.path.join(exp_path, "progress.csv")
            progress_json_path = os.path.join(exp_path, "progress.json")
            if os.path.exists(progress_csv_path):
                progress = load_progress(progress_csv_path, verbose=verbose)
            elif os.path.exists(progress_json_path):
                progress = load_progress(progress_json_path, verbose=verbose)
            else:
                continue
            if os.path.exists(variant_json_path):
                params = load_params(variant_json_path)
            else:
                params = dict(exp_name="experiment")
            exps_data.append(
                AttrDict(
                    progress=progress, params=params, flat_params=flatten_dict(params)
                )
            )
        except (IOError, pandas.errors.EmptyDataError) as e:
            if verbose:
                print(e)

    # a dictionary of all keys and types of values
    all_keys = dict()
    for data in exps_data:
        for key in data.flat_params.keys():
            if key not in all_keys:
                all_keys[key] = type(data.flat_params[key])

    # if any data does not have some key, specify the value of it
    if not ignore_missing_keys:
        default_values = dict()
        for data in exps_data:
            for key in sorted(all_keys.keys()):
                if key not in data.flat_params:
                    if key not in default_values:
                        default = None
                        default_values[key] = default
                    data.flat_params[key] = default_values[key]

    return exps_data


def smart_repr(x):
    if isinstance(x, tuple):
        if len(x) == 0:
            return "tuple()"
        elif len(x) == 1:
            return "(%s,)" % smart_repr(x[0])
        else:
            return "(" + ",".join(map(smart_repr, x)) + ")"
    else:
        if callable(x):
            return "__import__('pydoc').locate('%s')" % (
                x.__module__ + "." + x.__name__
            )
        else:
            return repr(x)


def extract_distinct_params(
    exps_data, excluded_params=("exp_name", "seed", "log_dir"), minimum=1
):
    try:
        stringified_pairs = sorted(
            map(
                eval,
                unique(
                    flatten(
                        [
                            list(map(smart_repr, list(d.flat_params.items())))
                            for d in exps_data
                        ]
                    )
                ),
            ),
            key=lambda x: (tuple("" if it is None else str(it) for it in x),),
        )
    except Exception as e:
        print(e)
        import ipdb

        ipdb.set_trace()
    proposals = [
        (k, [x[1] for x in v])
        for k, v in itertools.groupby(stringified_pairs, lambda x: x[0])
    ]
    filtered = [
        (k, v)
        for (k, v) in proposals
        if len(v) > minimum
        and all([k.find(excluded_param) != 0 for excluded_param in excluded_params])
    ]
    return filtered


class Selector(object):
    def __init__(self, exps_data, filters=None, custom_filters=None):
        self._exps_data = exps_data
        if filters is None:
            self._filters = tuple()
        else:
            self._filters = tuple(filters)
        if custom_filters is None:
            self._custom_filters = []
        else:
            self._custom_filters = custom_filters

    def where(self, k, v):
        return Selector(
            self._exps_data, self._filters + ((k, v),), self._custom_filters
        )

    def custom_filter(self, filter):
        return Selector(self._exps_data, self._filters, self._custom_filters + [filter])

    def _check_exp(self, exp):
        # or exp.flat_params.get(k, None) is None
        return all(
            (
                (
                    str(exp.flat_params.get(k, None)) == str(v)
                    or (k not in exp.flat_params)
                )
                for k, v in self._filters
            )
        ) and all(custom_filter(exp) for custom_filter in self._custom_filters)

    def extract(self):
        return list(filter(self._check_exp, self._exps_data))

    def iextract(self):
        return filter(self._check_exp, self._exps_data)


# Taken from plot.ly
color_defaults = [
    "#1f77b4",  # muted blue
    "#ff7f0e",  # safety orange
    "#2ca02c",  # cooked asparagus green
    "#d62728",  # brick red
    "#9467bd",  # muted purple
    "#8c564b",  # chestnut brown
    "#e377c2",  # raspberry yogurt pink
    "#7f7f7f",  # middle gray
    "#bcbd22",  # curry yellow-green
    "#17becf",  # blue-teal
]


def hex_to_rgb(hex, opacity=1.0):
    if hex[0] == "#":
        hex = hex[1:]
    assert len(hex) == 6
    return "rgba({0},{1},{2},{3})".format(
        int(hex[:2], 16), int(hex[2:4], 16), int(hex[4:6], 16), opacity
    )
