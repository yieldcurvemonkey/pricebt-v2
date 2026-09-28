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
# Pure interface (DESIGN.md section 9.3: "full" port, no DEV rows): BacktestBaseEngine's abstract
# get_action_handler(action). GenericEngine (P3.5) is the one real subclass.
from __future__ import annotations

from abc import abstractmethod

from .action_handler import TActionHandler
from .actions import TAction


class BacktestBaseEngine:
    @abstractmethod
    def get_action_handler(self, action: TAction) -> TActionHandler:
        pass
