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
# Ported to pricebt from gs_quant 2.1.17 (Apache-2.0); see NOTICE. Changes: none
#
# Stub (DESIGN.md section 2.3 / section 9.3 "stubs" row): gs's Marquee-hosted flow-vol backtest
# engine, GS-server-only / out of scope in pricebt v2. The importable name exists (so
# `strategy.py`'s engine list, P3.4, can name it); constructing it raises. Use GenericEngine, which
# covers the same EqOption/EqVarianceSwap strategies through asset configs.
from __future__ import annotations

from ..errors import NotSupportedError


class EquityVolEngine:
    def __init__(self, *args, **kwargs):
        raise NotSupportedError(f"{type(self).__name__} is GS-server-only / out of scope in pricebt v2; use GenericEngine")
