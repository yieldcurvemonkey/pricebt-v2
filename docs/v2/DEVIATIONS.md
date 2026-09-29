# Deviations from gs_quant

Every behavioural difference from gs_quant 2.1.17 is listed here with an ID, matching
[`DESIGN.md`](DESIGN.md) §11. "gs" below means 2.1.17. Test names were found by grepping the test
suite for each ID (`grep -rn "DEV-<id>"` over `tests/` and, where that found nothing, over `src/`
followed by a manual look at the test file the marked line's behaviour belongs to); a row marked
"no dedicated test found" means the grep found no test that names that ID, not that the behaviour
is unexercised — most such rows are still reached by a broader scenario test.

## Triggers, utils, data

| ID | gs behaviour | pricebt behaviour | Test(s) |
|---|---|---|---|
| DEV-T1 | a `timedelta` duration crashes `get_final_date` | `create_date + duration` | `tests/test_backtest_utils.py` |
| DEV-T2 | module-global `final_date_cache`, never cleared | `clear_final_date_cache()` is called at the start of every `run_backtest` | `tests/test_backtest_utils.py::test_clear_final_date_cache_empties_it`; called by the `tests/conftest.py` isolation fixture every test |
| DEV-T3 | `holiday_calendar=None` → a GS calendar lookup | weekends only (`week_mask='1111100'`), no external data access; currency/exchange codes ignored with a one-time warning | `tests/test_relative_date.py` |
| DEV-T4 | `PeriodicTriggerRequirements.start_date=None` means "today" | means the backtest start, set after the grid is built | `tests/test_triggers.py` |
| DEV-T5 | `NotTriggerRequirements.calc_type` is always `simple` | inherits the child's `calc_type` | `tests/test_triggers.py` |
| DEV-T6 | `NotTriggerRequirements.__setattr__` drops every attribute except `trigger` | normal attributes | `tests/test_triggers.py` |
| DEV-T7 | Aggregate/Not `get_trigger_times` returns `[]` | the union of the children's times | `tests/test_triggers.py` |
| DEV-T8 | `AggregateTriggerRequirements()` with no triggers raises `TypeError` | `ValueError('triggers required')` | `tests/test_triggers.py` |
| DEV-T9 | an intraday schedule loops forever on a midnight wrap | stops at the wrap | `tests/test_triggers.py` |
| DEV-T10 | stateful trigger state persists across `run_backtest` calls | the engine calls `reset()` on every trigger requirement at run start, walking Aggregate/Not children | `tests/test_triggers.py` |
| DEV-T11 | Phase 3 zips parallel info lists positionally; Phase 5 accumulates every fired trigger's infos into one list | `trigger_infos: dict[ActionType, dict[date, info]]`; Phase 5 passes only this trigger's info | `tests/test_engine_smoke.py` |
| DEV-T12 | `EventTriggerRequirements.data_source=None` → a GS event calendar | `ValueError('data_source required')` | `tests/test_triggers.py` |
| DEV-T13 | `GenericDataSource` mutates the series, looks ahead on a date index, raises `TypeError` comparing `DatetimeIndex` with `date` | the R01§7.4 v2 spec | `tests/test_generic_data_source.py` |
| DEV-T14 | `get_available_engines` with zero triggers raises `TypeError` | `[GenericEngine()]` | `tests/test_triggers.py` |
| DEV-T15 | 1.5.4: `'1M'` means "Nth Monday" | units are lower-cased (as 2.1.17) | `tests/test_relative_date.py` |
| DEV-T16 | a list `holiday_calendar` raises `TypeError: unhashable` in the `get_final_date` cache | `run_backtest` and every action's `__post_init__` normalise it to a tuple | `tests/test_actions.py`, `tests/test_backtest_utils.py`, `tests/test_engine_holidays.py` |

## Engine

| ID | gs behaviour | pricebt behaviour | Test(s) |
|---|---|---|---|
| DEV-E1 | a path-dependent trigger is evaluated before the day's risks are ensured | `ensure(d)`, then `has_triggered(d)` | `tests/test_engine_risk_trigger.py` |
| DEV-E2 | a simple trigger is re-evaluated once per path-dependent action | evaluated once per date; the `TriggerInfo` is cached | no dedicated test found; exercised wherever a path-dependent trigger drives more than one action per date |
| DEV-E3 | a skipped hedge/weighted-trade instrument still books its transaction-cost entries | its entry and exit TCEs are removed | `tests/test_engine_weighted_trade.py` |
| DEV-E4 | the initial portfolio's exit payment uses the un-renamed, unresolved instrument, adding a spurious `Open 9999-12-31` ledger row | uses the renamed, resolved instrument; no spurious row | no dedicated test found |
| DEV-E5 | `ExitTradeAction(priceable_names)` splits names on `_`, crashes with held hedges, breaks on underscores in names | `position_meta = (action_name, priceable_name, create_date)`, matched without string-splitting | `tests/test_actions.py`, `tests/test_engine_exit_trade.py` |
| DEV-E6 | `RebalanceActionImpl` appends the Python builtin `exit`, crashes | appends the exit TCE | `tests/test_engine_rebalance.py` |
| DEV-E7 | `if not Portfolio:` never fires (the intended guard is dead code) | `if portfolio is None: raise RuntimeError(...)` | `tests/test_actions.py` |
| DEV-E8 | `backtest.results[d]` on the defaultdict silently creates `[]` entries | `.get(d)` | no dedicated test found |
| DEV-E9 | a hedge's `trade_duration` attribute is looked up on the Portfolio, which fails | looked up on the single hedge instrument when the portfolio has one leaf | no dedicated test found |
| DEV-E10 | `ExitTradeAction.priceable_names` as a bare string is substring-matched; typo `priceables_names` | `make_list(priceable_names)` | `tests/test_actions.py` |
| DEV-E11 | `initial_value` appears only from the first cash-payment date, in that payment's currency | seeded before the cash walk, currency chosen per DESIGN §7 point 7 | `tests/test_multi_currency.py::test_initial_value_currency_selection_order` (currency choice), `tests/test_multi_currency.py::test_initial_value_seeded_at_grid_first_not_first_cash_payment_date_dev_e11` (date axis) |
| DEV-E12 | `dt.date.today()` guards | compared with the backtest end date (`_BACKTEST_END`) | `tests/test_transaction_costs.py` |
| DEV-E13 | cash currency via `map_ccy_name_to_ccy` of the unit's long name (unknown → `None`) | the ISO code taken from the value's unit | `tests/test_multi_currency.py` (asserts `cash_dict` keyed by `"USD"`/`"EUR"`) |
| DEV-E14 | risk list and PRR `risk_measures` built from `set(...)`, non-deterministic column order | ordered de-duplication | `tests/test_result_shapes.py` |
| DEV-E15 | multi-currency only through server-side `result_ccy` | `result_ccy` converts through the FX config; otherwise gs's errors, with a hint | `tests/test_multi_currency.py` |
| DEV-E16 | gs prices every weekday server-side | missing-market handling for grid and off-grid dates | `tests/test_missing_market.py` |
| DEV-E17 | `ExitTradeActionImpl` relocates the exited position's TransactionCostEntry unconditionally; crashes `list.remove(x): x not in list` exiting an initial_portfolio position (its CashPayments carry no TCE, DEV-E4) | skipped when there is no TCE to relocate | `tests/test_engine_exit_trade.py::test_exit_trade_action_on_an_initial_portfolio_position_does_not_crash_dev_e17` |
| DEV-E18 | `result_ccy`'s server-side conversion never reaches a `ScaledTransactionModel(scaling_type=<RiskMeasure>)`; the transaction cost prices in the measure's own currency and `Total` silently sums it with the converted Price/Cash | a `ParameterisedRiskMeasure` scaling_type is rewritten to `scaling_type(currency=result_ccy)` (mirrors DEV-E15) via a `_RESULT_CCY` ContextVar set for the run | `tests/test_multi_currency.py::test_scaled_transaction_model_risk_measure_scaling_type_converts_to_result_ccy_dev_e18` |
| DEV-E19 | a `PnlAttribute` has one market metric (`k·R·Δm` or `½·k·R·Δm²`), so a cross term such as vanna cannot be expressed | `PnlAttribute.cross_market_data_metric=None` appended: when set, `k·R(t−1)·Δm₁·Δm₂` (no ½), `m₂` read like `m₁`; `get_risks()` includes it and omits `None` metrics; `second_order` with a cross metric raises `ValueError` | `tests/test_pnl_ir.py::test_cross_term_known_answer_and_first_and_second_order_match_gs_s1_dev_e19`, `::test_pnl_attribute_appends_optional_fields_and_get_risks_omits_none_dev_e19_e21`, `::test_second_order_with_a_cross_metric_raises` |
| DEV-E20 | `pnl_explain` raises `KeyError` when a trade exits off the grid while other positions continue (R15 S3) | DEV-R1 prices the continuing positions on that date, so the step attributes correctly (the loop is gs's) | `tests/test_pnl_ir.py::test_i_off_grid_exit_while_another_position_is_held_attributes_correctly_dev_e20` |
| DEV-E21 | nothing checks a level's unit, so a definition for bp levels silently mis-scales decimal ones by 10⁴ | `PnlAttribute.market_data_unit`/`cross_market_data_unit=None` appended: when set, a level whose `FloatWithInfo.unit != {u: 1}` raises `ValueError` naming the attribute, measure, instrument and both units; the `ir_*`/`swaption_*`/`bond_pnl_definition` helpers fill them | `tests/test_pnl_ir.py::test_unit_check_names_attribute_measure_instrument_and_units_dev_e21`, `::test_f_decimal_levels_under_the_bp_definition_raise_naming_the_measure` |

## Results

| ID | gs behaviour | pricebt behaviour | Test(s) |
|---|---|---|---|
| DEV-R1 | `result_summary` ffills the previous PV onto a flat date while cash already includes the exit proceeds, so `Total` double-counts | a flat date gets PV 0 and risk 0 before the ffill; a non-flat non-grid date also prices continuing positions into that row | `tests/test_engine_periodic_roll.py`, `tests/test_result_shapes.py` (flat-date zeroing half); `tests/test_engine_smoke.py::test_non_grid_non_flat_date_prices_the_continuing_position_fresh_dev_r1` (non-flat, non-grid continuing-pricing half — test_result_shapes.py's hand-built fixtures cannot reach this) |
| DEV-R2 | `get_risk_summary_df` is computed once and never invalidated | recomputed on each call | `tests/test_result_shapes.py` |
| DEV-R4 | bucketed cells are ffilled on flat dates | zeroed, following DEV-R1 | `tests/test_result_shapes.py::test_case_b_bucketed_irdelta_is_zero_on_flat_date_not_ffilled_dev_r4` |
| DEV-R5 | bucketed frames ordered by `sort_risk`/`point_sort_order` (asset-class regexes) | the config's bucket order, first appearance across groups | `tests/test_risk_results.py` |
| DEV-R6 | `PortfolioRiskResult.to_frame` pairs depth-first leaf records with breadth-first portfolio labels, so rows are mislabelled when a level mixes leaves and sub-portfolios | every record is labelled from its own path (`portfolio_name_{k}`, `instrument_name`), depth-first in `futures` order | `tests/test_results_parity.py::test_to_frame_raw_records_are_labelled_from_their_own_paths_dev_r6`; `tests/test_portfolio_notebooks.py::test_030009_nested_to_frame_pivots` |
| DEV-R7 | `PortfolioRiskResult.transform` on a nested result rebuilds one future per leaf, misaligned with the nested portfolio; `subset` of a single sub-portfolio path pairs it with one future; `prr[PortfolioPath]` raises `KeyError` | the tree is kept (one nested result per sub-portfolio); a single sub-portfolio path returns that nested result; `prr[PortfolioPath]` returns the member at that path | `tests/test_results_parity.py::test_transform_keeps_the_nested_tree_dev_r7`, `::test_list_of_instruments_and_slice_give_subsets`, `::test_getitem_name_and_instrument_first_match_at_any_depth` |
| DEV-R8 | `PortfolioRiskResult + number` raises `RuntimeError('... cannot be composed')` | `ValueError('Can only add instances of PortfolioRiskResult')` | `tests/test_results_parity.py::test_add_same_date_overlap_raises_and_numbers_are_rejected_dev_r8` |
| DEV-R9 | `MultipleRiskMeasureResult * k` on a historical Series raises `AttributeError`; `* non-number` *returns* a `ValueError`; `PortfolioRiskResult * k` also multiplies string label columns; `MultipleRiskMeasureResult +` one of another instrument builds a `PortfolioRiskResult` over `Portfolio((i1, i2))` | a Series is multiplied directly; a non-number raises `ValueError`; only a bucketed frame's `value` column is scaled, and a table's scale columns; different instruments raise `NotSupportedError` (`risk.results` may not import `Portfolio`) | `tests/test_results_parity.py::test_mul_scales_every_leaf_and_rejects_non_numbers_dev_r9`, `::test_mrmr_constructor_dates_and_date_indexing`, `::test_mrmr_add_composes_and_rejects_other_instruments`, `::test_scaling_a_table_scales_its_scale_columns_only_dev_r9` |
| DEV-R10 | `subtract_risk` asserts `'value' in left.columns.names`, so it always raises `AssertionError` | `aggregate_risk((left, -right))`; needs identical columns including `value`, else `ValueError` | `tests/test_results_parity.py::test_core_helpers` |
| DEV-R11 | no table (frame-valued) measures | a table is a `DataFrameWithInfo` with `pricebt_table = True`: `PortfolioRiskResult.to_frame` pivoted on `value` leaves tables out; the default `to_frame` shows them indexed by their own columns; `aggregate()`/`aggregate_results` concatenate tables (adding `instrument_name`), never sum them; a historical table is one table with a `date` column first, marked historical by its priced dates (never by a column name). The `BackTest` views (`get_risk_summary_df`, `result_summary`, `risk_summary`, `strategy_as_time_series`, `summary_stats`) skip table measures, found by their results' marker, never by name; `pnl_bps` of one raises a `ValueError` saying it is a table measure (IR_RISK_DESIGN R2-15) | `tests/test_pnl_ir.py::test_h_views_skip_the_cashflows_table_dev_r11`; `tests/test_results_parity.py::test_to_frame_value_pivot_drops_table_measures_dev_r11`, `::test_aggregate_historical_bucketed_and_tables`, `::test_a_table_is_historical_by_its_priced_dates_never_by_a_date_column`; `tests/test_table_results.py::test_historical_frame_is_one_table_with_a_date_column_per_date`, `::test_historical_multi_measure_keeps_series_for_numbers_and_a_table_for_frames` |
| DEV-R12 | `FloatWithInfo(risk_key, value, unit, error, request_id)` | value first: `FloatWithInfo(value, risk_key=None, unit=None, error=None)`; `StringWithInfo` and `DictWithInfo` follow the same order | `tests/test_results_parity.py::test_float_with_info_arithmetic_follows_gs`, `::test_other_value_classes` |
| DEV-R13 | `PortfolioRiskResult +` over different portfolios with several measures fills a leaf's missing measure from the other result where it holds the same instrument (`set_value`), mutating a multi-measure input in place | leaves are wrapped per measure (gs `as_multiple_result_futures`) but nothing is filled in: reading a measure a leaf lacks raises `KeyError`, never another measure's value | `tests/test_results_parity.py::test_add_of_different_portfolios_and_measures_never_serves_one_measure_for_another_dev_r13` |
| DEV-R14 | `FloatWithInfo + number` and `sum()` give plain floats; `+` raises "unit mismatch" unless the units are equal | `+ number` and `sum()` keep a `FloatWithInfo`; a `None` unit adds to any unit; unequal non-None units still raise | `tests/test_results_parity.py::test_float_with_info_keeps_its_type_under_sum_and_a_none_unit_adds_dev_r14` |
| DEV-R15 | `aggregate()` with no leaves returns `None`; `aggregate_results` of plain floats raises `AttributeError` | `FloatWithInfo(0.0)`; plain floats sum like `FloatWithInfo`s | `tests/test_results_parity.py::test_aggregate_of_no_leaves_is_zero_and_plain_floats_sum_dev_r15` |
| DEV-R16 | a historical frame has no row for a date whose ladder was empty, so `result[date]` raises `KeyError`; an all-empty result is returned undated | composed frames carry their priced dates: a priced date with no rows gives an empty frame carrying the date (IR_RISK_DESIGN R2-27); an unpriced date raises `KeyError` as gs | `tests/test_results_parity.py::test_a_date_the_history_was_not_priced_on_is_a_key_error_dev_r16`, `::test_date_indexing_dispatches_per_leaf_so_an_all_empty_history_still_slices`, `::test_hedge_on_a_ladder_that_is_empty_on_some_dates_r2_27`; `tests/test_portfolio_pricing.py::test_historical_bucketed_value_is_one_date_indexed_frame`, `::test_hedge_with_a_leg_whose_ladder_is_empty_on_some_dates_r2_27` |

(DEV-R3 from revision 1 was removed: 2.1.17 already returns an empty frame when there are no results.)

## Instruments / risk

| ID | gs behaviour | pricebt behaviour | Test(s) |
|---|---|---|---|
| DEV-I1 | scaling edits size fields (`notional_amount`, `pay_or_receive`, `fee`) in place | a signed `quantity_` multiplier; kwargs never edited. pricebt also scales classes gs cannot (gs `Bond.scale()` raises) | `tests/test_instrument.py`; `tests/test_instrument.py::test_bond_is_a_generated_class_that_scales_via_quantity` |
| DEV-I2 | `strategy_as_time_series` static data shows the resolved gs fields | resolved terms plus a `quantity_` column | `tests/test_result_shapes.py` |
| DEV-I3 | `ExitTradeAction` compares instruments by `to_dict()` set-membership (an unhashable dict; a latent crash) | compares by `instrument_identity()` tuple | `tests/test_portfolio.py` (identity/name dedup); no test isolates the `ExitTradeActionImpl` call site by this ID |
| DEV-I4 | `IRDelta(aggregation_level=Type)` returns a small DataFrame | a `FloatWithInfo` for Type/Asset/Class; a bucketed frame for None/Point | `tests/test_pricing_service.py` |
| DEV-I5 | an unparameterised currency-bearing risk is in USD (per the gs `IRDelta` docstring) | the function's own currency (decision 0.5) | no dedicated test found |
| DEV-I6 | instrument strings (`'100k'`, `'ATM+25'`, `'=solvefor(...)'`) are parsed server-side | not parsed by pricebt; the asset's `resolve` decides | none — documentation only, per DESIGN §11 |
| DEV-I7 | `IRFwdRate` is always in percent | whatever unit the asset function declares (the shipped configs use bp); intensive units are not multiplied by quantity | `tests/test_pricing_service.py` |
| DEV-I8 | measure parameters beyond currency and aggregation level are honoured server-side | narrowed by DEV-I10: `NotSupportedError` for `mkt_marking_options`, and for a pass-through parameter the chosen function does not reference | `tests/test_pricing_service.py::test_irdelta_bump_size_raises_not_supported` (unreferenced parameter); `tests/test_pricing_params.py::test_mkt_marking_options_always_raises` |
| DEV-I9 | 1.5.4: `IRVanna`/`IRVolga` are plain, non-callable `RiskMeasure` | `RiskMeasureWithFiniteDifferenceParameter` (as 2.1.17), so `IRVanna(aggregation_level=Type)` works | `tests/test_risk_measures.py::test_vanna_volga_are_finite_difference_measures`; `tests/test_gs_api_parity.py::test_risk_measure_exception_expect_is_enforced` |
| DEV-I10 | `bump_size`, `finite_difference_method`, `local_curve`, `scale_factor` are sent to the GS server | injected as `pricebt_<name>` (None when unset) into `functions:`/`portfolio_functions:`; a function supports one iff its expression names it, else `NotSupportedError`; part of every cache and group key | `tests/test_pricing_params.py::test_bump_size_reaches_the_function_and_is_part_of_the_unit_value_cache_key`, `::test_a_parameter_the_function_does_not_reference_is_refused_naming_the_variable`, `::test_portfolio_aggregate_with_two_bump_sizes_never_shares_the_portfolio_value_cache`, `::test_lazy_group_key_and_member_carry_the_params` |
| DEV-I11 | any measure is answered; an inapplicable one silently returns `UnsupportedValue` | IRSwap/IRSwaption/Bond configs must map or declare (`unsupported_measures:`, with a reason) every contract measure and form, checked at load (one error listing every gap, with a paste-ready block); mapped and declared → `UserWarning`, the mapping wins; requesting a declared form with an empty mapping slot raises `UnsupportedMeasureError`; a preset or fallback key the contract counts also serves the base measure's request; a declaration the contract cannot count (preset name, form outside the row, unknown name) warns | `tests/test_contracts.py::test_one_config_error_lists_every_problem_and_ends_with_the_block`, `::test_paste_ready_block_makes_the_config_load`, `::test_mapped_and_declared_warns_at_load`, `::test_declaring_a_preset_name_warns_to_declare_the_base`, `::test_declaring_a_form_outside_the_contract_row_warns`, `::test_declaring_an_unknown_name_warns_with_a_suggestion`; `tests/test_unsupported_measures.py::test_declared_whole_measure_raises_naming_measure_and_reason`, `::test_mapping_wins_over_a_stale_whole_measure_declaration`, `::test_a_preset_key_serves_its_base_in_the_form_the_contract_counts`, `::test_an_alias_mapping_wins_over_a_declaration_of_its_base` |
| DEV-I12 | IR delta/gamma are curve sensitivities; `IRFwdRate` is defined for swaps/swaptions only | own-rate semantics: `IRDelta` scalar = total derivative of `Price` w.r.t. the own quoted rate (`IRFwdRate`, incl. a bond's yield to maturity), `IRGammaParallel` the chain-rule second derivative; summing `IRDeltaParallel` across different instrument types is approximate. The shipped toy and ARBS swap configs' `IRDelta` scalar is a fixed-annuity pv01: **at-the-money-exact** only (off-market it misses `N·(F−K)·ΔA`), which the load check cannot detect | contract text in `src/pricebt/risk/contracts.py`; numbers on the toys: `tests/test_toylib_ir.py::test_gamma_is_the_derivative_of_the_total_delta_in_the_own_rate`, `::test_swaption_delta_is_the_total_derivative_in_the_forward`, `::test_swap_off_market_delta_is_not_the_annuity_pv01`, `::test_bond_schedule_price_and_yield_round_trip` (`IRFwdRate` is the yield to maturity) |
| DEV-I13 | `IRGamma` returns a 12-column cross-gamma frame | `IRGamma` (bucketed only) is a diagonal gamma ladder: the 6-column bucketed frame, ccy per bp² at each pillar | contract text in `src/pricebt/risk/contracts.py`; ladders on the toys: `tests/test_toylib_ir.py::test_swap_levels_and_ladders`, `::test_levels_and_ladders_of_the_swaption`, `::test_bond_dv01_and_ladders` |
| DEV-I14 | `Base.__setattr__` coerces a field and writes it on the dataclass, resolved or not | camelCase is normalised to snake_case; a gs field other than `name`, or a name already in `_kwargs` (a `ConfigInstrument` term, an extra kwarg), is coerced and written to `_kwargs`, the terms pricing reads (None deletes the key); a resolved instrument raises `ValueError` (the `clone` rule); `asset_class`/`type_` raise `'<key> cannot be set'` as in gs; every other name is a plain attribute | `tests/test_instrument.py::test_setting_a_gs_field_writes_kwargs_coerced_and_none_deletes`, `::test_setting_a_gs_field_on_a_resolved_instrument_raises`, `::test_asset_class_and_type_cannot_be_set`, `::test_non_field_names_are_plain_attributes`, `::test_setting_a_field_changes_the_toy_price`, `::test_setting_a_term_already_in_kwargs_writes_kwargs`, `::test_setting_a_config_instrument_term_changes_the_toy_price` |
| DEV-I15 | IR `Theta` is undocumented | one calendar day of carry, total return, own rate and vol held fixed, ccy per day (`IRTheta` = 365 × `Theta`) | contract text in `src/pricebt/risk/contracts.py`; frozen-world identity on the toys: `tests/test_toylib_ir.py::test_frozen_world_theta_identity_swap_and_swaption`, `::test_frozen_world_theta_identity_bond_including_a_coupon_step` |
| DEV-I16 | `IRGammaParallelLocalCcy` / `IRDiscountDeltaParallelLocalCcy` need their own mapping | they carry `base_name` `IRGammaParallel` / `IRDiscountDeltaParallel`, so the base measure's mapping serves them | `tests/test_risk_measures.py::test_local_ccy_fallback_base_name`, `::test_only_presets_and_local_ccy_fallbacks_carry_a_base_name`; `tests/test_unsupported_measures.py::test_declaration_is_found_through_base_name` |
| DEV-I17 | `ExpiryInYears` is defined for options only | `max(final_or_expiry − t, 0).days / 365` for every class (swaps and bonds use the final date) | contract text in `src/pricebt/risk/contracts.py`; `tests/test_toylib_ir.py::test_expiry_in_years_for_all_three_classes` |
| DEV-I18 | reading a gs field that was never set returns `None` | raises `AttributeError(field)`, so an unset term is loud and `hasattr` is False | `tests/test_instrument.py::test_reading_an_unset_gs_field_raises_dev_i18` |

## Portfolio

| ID | gs behaviour | pricebt behaviour | Test(s) |
|---|---|---|---|
| DEV-P1 | `Portfolio.all_portfolios` returns only the direct sub-portfolios; `Portfolio.__contains__` looks in itself and those only, with bare `==` | `all_portfolios` recurses and de-duplicates; `Portfolio.__contains__` and `PortfolioRiskResult.__contains__` match at any depth, and also through `.unresolved` (as gs `paths` does) | `tests/test_portfolio.py::test_contains_and_all_portfolios_reach_every_depth_dev_p1`; `tests/test_results_parity.py::test_contains_at_any_depth` |

## Markets

| ID | gs behaviour | pricebt behaviour | Test(s) |
|---|---|---|---|
| DEV-M1 | `PricingContext(market=...)` prices on that market | any non-None `market` raises `NotSupportedError` at construction (it used to be silently ignored); the P&L-explain phase relaxes this for `CloseMarket` | `tests/test_contexts.py::test_any_market_raises_not_supported_dev_m1` |

## Kept on purpose ("parity kept")

- ALL_OF short-circuits, while ANY_OF evaluates every child.
- `Portfolio.__len__` counts dates, not instruments.
- `check_barrier` uses strict `>`/`<`.
- The mean-reversion window excludes today, uses the sample std (`ddof=1`), and a zero std gives
  `inf`, which fires the trigger.
- `get_data_range` is `(start, end]`; an int means the last N points strictly before `end`.
- Hedges placed on one date are all sized against the same pre-hedge risk.
- The global `Action{N}` counter (not reset per strategy instance, only per `run_backtest` call).
- The holding window is `create_date <= s < final_date`.
- Entry cash is `−PV(create)` and exit cash `+PV(final)`; coupons paid while a trade is held are
  not booked between marks.
- `trade_ledger`'s `Long Short` column is the direction of the opening payment.

## pricebt-only additions (no gs equivalent)

These exist because pricebt has no server, so gs facilities backed by GS infrastructure need a
local replacement, or because MUST-4 (multi-currency, bp units) needed a new view:

- **`pnl_bps(risk)`** (`BackTest`, DESIGN §8.4): P&L expressed in bp of rate move — each row's P&L
  divided by the *previous* row's risk — so a book's performance can be read independent of
  notional or currency. Tested in `tests/test_pnl_bps.py`.
- **`pnl_explain_table()`** (`BackTest`) and **`ir_pnl_definition`/`swaption_pnl_definition`/
  `bond_pnl_definition`** (`pricebt.backtests.backtest_objects`, next to `fx_pnl_definition`;
  IR_RISK_DESIGN §7): the per-step table of actual, cashflow, economic, per-attribute, explained
  and residual P&L over `pnl_explain`'s own steps and held set (its attribute columns' cumsum is
  `pnl_explain()` exactly), and IR definitions whose scaling factors and unit checks come from the
  declared rate/vol units. The table raises `ValueError` on attribute names that repeat or equal a
  fixed column, and on a step whose prices or due cashflows are in different currencies (as
  `result_summary` does); `pnl_explain()` keeps gs's unchecked loop. Tested in `tests/test_pnl_ir.py`.
- **`measure_series(...)`** (`pricebt.data`, DESIGN §10): replaces the GS `Dataset` for feeding a
  `GenericDataSource` — resolves a fresh instrument copy on each date of a schedule and evaluates
  one function or risk measure. Tested in `tests/test_measure_series.py`.
- **`missing_market` handling** (`PricebtSession`, DESIGN §9.5, DEV-E16): gs has no notion of a
  market being unavailable, because its data is server-side and always answers. pricebt's
  `missing_market='drop'` policy removes grid dates with no market (with a warning, and a listing
  on `backtest.missing_market_dates`/`missing_market_moves`) and rolls off-grid valuation dates
  forward to the next kept grid date. Tested in `tests/test_missing_market.py`.
- **`PricebtSession`** (`pricebt.session`, DESIGN §6.1): replaces `GsSession` as the place a
  notebook registers its asset configs (`PricebtSession.use(assets=[...])`) and, optionally, a
  `reporting_currency` for `initial_value` seeding (DEV-E11). `GsSession.use` itself survives as a
  no-op shim with the gs signature, for notebooks that still call it.
- **`ConfigInstrument`** (`pricebt.instrument`, DESIGN §5.1): a generic instrument class for an
  asset with no dedicated gs class — kwargs pass straight through to the config with no gs field
  list at all.
- **`quantity_`** (`pricebt.instrument`, DEV-I1): pricebt's position-size multiplier, replacing
  gs's in-place scaling of size fields (`notional_amount`, etc.). It is gs's own
  position-multiplier name internally (`base.py:577`), reused here as the *only* scaling
  mechanism, so a config's kwargs are never rewritten by the engine.
- **`pricebt_asset`** (`pricebt.instrument`, DESIGN §5.1): an explicit, keyword-only override of
  asset-config matching, for when an instrument's kwargs are ambiguous between two configs.
