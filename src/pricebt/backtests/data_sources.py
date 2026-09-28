"""
Copyright 2019 Goldman Sachs.
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
"""
# Ported to pricebt from gs_quant 2.1.17 (Apache-2.0); see NOTICE. Changes: DEV-T13
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Iterable, List, Union

import numpy as np
import pandas as pd

from ..base import static_field
from ..errors import NotSupportedError


class MissingDataStrategy(Enum):
    fill_forward = 'fill_forward'
    interpolate = 'interpolate'
    fail = 'fail'


@dataclass
class DataSource:
    __sub_classes: ClassVar[List[type]] = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        DataSource.__sub_classes.append(cls)

    @staticmethod
    def sub_classes():
        return tuple(DataSource.__sub_classes)

    def get_data(self, state, **kwargs):
        raise RuntimeError("Implemented by subclass")

    def get_data_range(self, start: Union[dt.date, dt.datetime], end: Union[dt.date, dt.datetime, int], **kwargs):
        raise RuntimeError("Implemented by subclass")


@dataclass
class GsDataSource(DataSource):
    """gs's server-side dataset-backed source. Out of scope: pricebt has no GS server. Field shape
    kept for constructor parity (DESIGN.md MUST-2 / section 9.3: "GsDataSource ... -> stub"); build
    a `GenericDataSource` from your own pandas Series instead."""

    data_set: str
    asset_id: str
    min_date: dt.date = None
    max_date: dt.date = None
    value_header: str = 'rate'
    class_type: str = static_field('gs_data_source')

    def __post_init__(self):
        raise NotSupportedError("GsDataSource is GS-server-only / out of scope in pricebt v2; use GenericDataSource")


@dataclass
class GenericDataSource(DataSource):
    """
    A data source which holds a pandas series indexed by date or datetime.

    :param data_set: a pandas series indexed by date or datetime
    :param missing_data_strategy: MissingDataStrategy which defines behaviour if data is missing,
                                  will only take effect if using get_data, get_data_range has no
                                  expectations of the number of expected data points.

    gs's version mutates `self.data_set` in place on every miss (permanently inserting a NaN and
    reassigning the ffilled/interpolated series back onto itself), looks ahead when forward-filling
    an object-dtype date index (the missing point is appended at the end and the re-sorted result
    discarded before `ffill()`, so the value returned is the LAST point in the series, not the
    previous one), and raises `TypeError` comparing a `DatetimeIndex` with a `dt.date` in
    `get_data_range`. None of that ports over here: see the `# pricebt DEV-T13` markers below.
    """

    data_set: pd.Series = None
    missing_data_strategy: MissingDataStrategy = MissingDataStrategy.fail
    class_type: str = static_field('generic_data_source')

    def __eq__(self, other):
        if not isinstance(other, GenericDataSource):
            return False
        return self.missing_data_strategy == other.missing_data_strategy and self.data_set.equals(other.data_set)

    # pricebt DEV-T13 (research/01 section 7.4 "v2 specification"): normalise a private, sorted
    # copy of the series ONCE here instead of gs's per-miss in-place mutation of self.data_set.
    # self.data_set is left exactly as given (so __eq__ keeps gs's semantics and the input is
    # never mutated); every state/start/end argument is coerced to this same key type (_to_key)
    # before it is ever compared against the index, which is what removes the look-ahead bug and
    # the DatetimeIndex-vs-date TypeError.
    def __post_init__(self):
        index = list(self.data_set.index)
        self._tz_aware = isinstance(index[0], dt.datetime) and index[0].tzinfo is not None
        date_only = not self._tz_aware and all(isinstance(k, dt.date) and not isinstance(k, dt.datetime) for k in index)
        self._key_type = dt.date if date_only else pd.Timestamp
        normalised = pd.Series(list(self.data_set.values), index=[self._to_key(k) for k in index])
        self._normalised = normalised.sort_index()

    def _to_key(self, state):
        """Coerce a state/start/end argument to this source's normalised index key type."""
        if self._key_type is dt.date:
            return state.date() if isinstance(state, dt.datetime) else state
        ts = pd.Timestamp(state)
        if self._tz_aware and ts.tzinfo is None:
            # gs step 3: a naive time is labelled UTC, not converted.
            ts = ts.tz_localize(dt.timezone.utc)
        return ts

    def get_data(self, state: Union[dt.date, dt.datetime, Iterable]):
        """
        Get the value of the dataset at a time or date. If a list of dates or times is provided
        return a list of values.
        :param state: a date, datetime, or a list of dates or datetimes; None returns the whole series
        :return: float value, list of float values, or the whole (normalised) pd.Series
        """
        if state is None:
            return self._normalised
        if isinstance(state, Iterable):
            # kept as gs has it: a str is Iterable too and fans out char-by-char (R01 section 7.4
            # step 2 calls this a quirk; the v2 specification's four numbered items do not remove
            # it, so it is not a behavioural change and carries no marker of its own).
            return [self.get_data(i) for i in state]

        key = self._to_key(state)
        if key in self._normalised.index:
            return self._normalised[key]
        if self.missing_data_strategy == MissingDataStrategy.fail:
            raise KeyError(state)
        if self.missing_data_strategy == MissingDataStrategy.fill_forward:
            # pricebt DEV-T13: the previous value strictly before `key`, never a later one (gs's
            # look-ahead bug: it appended the NaN at the end of the object-index series, discarded
            # the sort, then ffilled -- returning the LAST value in the series instead).
            pos = self._normalised.index.searchsorted(key, side='right') - 1
            return self._normalised.iloc[pos] if pos >= 0 else np.nan
        if self.missing_data_strategy == MissingDataStrategy.interpolate:
            extended = pd.concat([self._normalised, pd.Series([np.nan], index=[key])]).sort_index()
            return extended.interpolate().loc[key]
        raise RuntimeError(f'unrecognised missing data strategy: {str(self.missing_data_strategy)}')

    def get_data_range(self, start: Union[dt.date, dt.datetime], end: Union[dt.date, dt.datetime, int]):
        """
        Get a range of values from the dataset.
        :param start: a date or datetime
        :param end: a date, datetime, or an int. If an int is provided we return that many data
                    points strictly before the start date (index < start).tail(end); otherwise we
                    return the points with start < index <= end.
        :return: pd.Series
        """
        # pricebt DEV-T13: start/end are coerced through _to_key to the source's normalised key
        # type before comparison, so a dt.date against a DatetimeIndex-backed source never raises
        # (gs: `TypeError: Invalid comparison between dtype=datetime64[ns] and date`).
        start_key = self._to_key(start)
        if isinstance(end, int):
            return self._normalised.loc[self._normalised.index < start_key].tail(end)
        end_key = self._to_key(end)
        return self._normalised.loc[(start_key < self._normalised.index) & (self._normalised.index <= end_key)]


@dataclass
class DataManager:
    """gs's PredefinedAssetEngine/DataHandler data registry (keyed by frequency, instrument name,
    valuation type). Out of scope: pricebt v2 has no PredefinedAssetEngine; use GenericDataSource
    with GenericEngine instead."""

    def __post_init__(self):
        raise NotSupportedError("DataManager is GS-server-only / out of scope in pricebt v2; use GenericDataSource")
