"""P2.1: pricebt.markets.portfolio.Portfolio (DESIGN.md section 5, research/04 section 5)."""
from __future__ import annotations

import pytest

from pricebt.instrument import IRSwap
from pricebt.markets.portfolio import Portfolio


def _resolved_swap(name, tenor, quantity_=1.0):
    swap = IRSwap("Pay", tenor, "USD", 1e4, name=name, quantity_=quantity_)
    swap._set_resolution({"termination_date": tenor}, None, None, None)
    return swap


# --------------------------------------------------------------------------------- construction forms


def test_construct_from_list_and_single_and_dict():
    a = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    b = IRSwap("Pay", "5y", "USD", 1e4, name="b")
    assert len(Portfolio([a, b])) == 2
    assert len(Portfolio(a)) == 1
    assert len(Portfolio(tuple([a, b]))) == 2

    by_dict = Portfolio({"x": IRSwap("Pay", "10y", "USD", 1e4), "y": IRSwap("Pay", "5y", "USD", 1e4)})
    assert [c.name for c in by_dict.priceables] == ["x", "y"]


def test_portfolio_wraps_a_portfolio_as_one_child_not_flattened():
    a = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    inner = Portfolio([a], name="hedge")
    outer = Portfolio(inner)
    assert len(outer) == 1
    assert outer.priceables[0] is inner
    assert outer.priceables[0].name == "hedge"


def test_nested_portfolio_survives_wrapping():
    a = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    nested = Portfolio(Portfolio([a], name="hedge"))
    assert nested.priceables[0].name == "hedge"
    assert nested.priceables[0].priceables[0] is a


# --------------------------------------------------------------------------------- traversal


def test_instruments_vs_all_instruments_and_dedup_by_identity_and_name():
    a = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    b = IRSwap("Pay", "5y", "USD", 1e4, name="b")
    inner = Portfolio([b], name="inner")
    p = Portfolio([a, a, inner])
    # `instruments` is direct children only, and excludes the nested Portfolio.
    assert p.instruments == (a,)
    # `all_instruments` walks nested Portfolios too, de-duplicating the repeated `a` (same
    # identity AND name -> Instrument.__eq__/__hash__ already fold name into identity).
    assert p.all_instruments == (a, b)


def test_all_instruments_identity_and_name_dedup_two_distinct_names_kept():
    a1 = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    a2 = IRSwap("Pay", "10y", "USD", 1e4, name="a2")  # same terms, different name -> NOT a duplicate
    p = Portfolio([a1, a2])
    assert len(p.all_instruments) == 2


def test_len_iter_getitem_contains():
    a = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    b = IRSwap("Pay", "5y", "USD", 1e4, name="b")
    p = Portfolio([a, b], name="book")
    assert len(p) == 2
    assert list(iter(p)) == [a, b]
    assert p[0] is a
    assert p[1:] == (b,)
    assert p["a"] is a
    assert "a" in p
    assert a in p
    assert "missing" not in p
    # gs: no match -> `()`, not KeyError (verified against gs_quant markets/portfolio.py
    # Portfolio.__getitem__).
    assert p["missing"] == ()


def test_getitem_list_key_collapses_to_bare_object_on_single_total_match():
    """gs's `__getitem__` flattens matches across every element of a list key into one tuple and
    applies the same single-match collapse as the scalar case -- so a list key with exactly one
    total match (even split across several list elements, some of which match nothing) returns
    the bare object, not a 1-tuple (verified against the REAL installed gs_quant 1.5.4
    `gs_quant.markets.portfolio.Portfolio.__getitem__`)."""
    a = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    b = IRSwap("Pay", "5y", "USD", 1e4, name="b")
    p = Portfolio([a, b], name="book")
    assert p[["a"]] is a
    assert p[["a", "missing"]] is a
    assert p[["a", "b"]] == (a, b)
    assert p[["missing"]] == ()


def test_empty_named_nested_portfolio_not_matched_by_name():
    """gs: `if i and i.name` -- a falsy (empty, len==0) nested Portfolio is never matched by name,
    even though its `.name` is set (verified against the REAL installed gs_quant 1.5.4
    `gs_quant.markets.portfolio.Portfolio` priceables setter)."""
    a = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    empty = Portfolio([], name="empty")
    outer = Portfolio([empty, a])
    assert outer.paths("empty") == ()
    assert "empty" not in outer
    assert outer["empty"] == ()


def test_paths_matches_by_name_and_by_object_recursively():
    a = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    inner = Portfolio([a], name="inner")
    p = Portfolio([inner])
    assert p.paths("a") == (a,)
    assert p.paths(a) == (a,)
    with pytest.raises(ValueError):
        p.paths(42)


# --------------------------------------------------------------------------------- eq / hash / add / mutation


def test_portfolio_eq_ignores_names_compares_leaves():
    a = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    b = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    assert Portfolio([a], name="p1") == Portfolio([b], name="p2")


def test_eq_is_asymmetric_like_gs():
    """gs's __eq__ walks only self's leaf paths against the same path in other, so a
    shorter/shallower self can equal a longer/deeper other but not vice versa (research/04
    section 5, verified against gs_quant markets/portfolio.py)."""
    a = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    b = IRSwap("Pay", "5y", "USD", 1e4, name="b")
    short = Portfolio([a])
    long_ = Portfolio([a, b])
    assert short == long_
    assert not (long_ == short)


def test_hash_matches_eq_for_distinct_objects_same_value():
    """Equal (by value) Portfolios must hash equal, whatever their `id()`."""
    a = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    p1 = Portfolio([a], name="book")
    p2 = Portfolio([a], name="book")
    assert p1 is not p2
    assert p1 == p2
    assert hash(p1) == hash(p2)


def test_add_concatenates_with_no_name():
    a = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    b = IRSwap("Pay", "5y", "USD", 1e4, name="b")
    combined = Portfolio([a], name="p1") + Portfolio([b], name="p2")
    assert combined.name is None
    assert len(combined) == 2


def test_add_non_portfolio_raises_value_error_matching_gs():
    a = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    with pytest.raises(ValueError, match=r"^Can only add instances of Portfolio$"):
        Portfolio([a]) + "not a portfolio"


def test_append_extend_pop():
    a = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    b = IRSwap("Pay", "5y", "USD", 1e4, name="b")
    p = Portfolio([a])
    p.append(b)
    assert len(p) == 2
    c = IRSwap("Pay", "2y", "USD", 1e4, name="c")
    p.extend([c])
    assert len(p) == 3
    popped = p.pop(b)
    assert popped is b
    assert b not in p


def test_append_accepts_iterable_of_priceables_as_direct_children():
    """gs: `self.priceables += (priceables,) if isinstance(priceables, PriceableImpl) else
    tuple(priceables)` -- appending a list adds each element as its own direct child, not one
    nested list object."""
    a = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    b = IRSwap("Pay", "5y", "USD", 1e4, name="b")
    c = IRSwap("Pay", "2y", "USD", 1e4, name="c")
    p = Portfolio([a])
    p.append([b, c])
    assert len(p) == 3
    assert p.priceables == (a, b, c)


def test_pop_missing_is_noop_returning_empty_tuple():
    a = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    p = Portfolio([a])
    assert p.pop("missing") == ()
    assert len(p) == 1
    assert p.priceables == (a,)


def test_pop_drops_nested_portfolio_rather_than_flattening():
    """gs's `pop` rebuilds from direct-children `instruments` (leaves only), not the recursive
    `all_instruments` -- a nested Portfolio child is dropped whole, not flattened into it
    (research/04 section 5, verified against gs_quant markets/portfolio.py Portfolio.pop)."""
    a = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    b = IRSwap("Pay", "5y", "USD", 1e4, name="b")
    inner = Portfolio([a], name="inner")
    top = Portfolio([inner, b])
    popped = top.pop(b)
    assert popped is b
    assert len(top) == 0


def test_clone_default_shares_instruments_clone_instruments_true_copies():
    a = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    p = Portfolio([a], name="book")
    shallow = p.clone()
    assert shallow.priceables[0] is a
    deep = p.clone(clone_instruments=True)
    assert deep.priceables[0] is not a
    assert deep.priceables[0] == a


# --------------------------------------------------------------------------------- scale


def test_scale_in_place_scales_every_leaf_and_returns_none():
    a = _resolved_swap("a", "10y")
    b = _resolved_swap("b", "5y")
    p = Portfolio([a, b])
    assert p.scale(2, in_place=True) is None
    assert a.quantity_ == 2.0 and b.quantity_ == 2.0


def test_scale_not_in_place_flattens_and_is_unnamed():
    a = _resolved_swap("a", "10y")
    inner = Portfolio([_resolved_swap("b", "5y")], name="inner")
    p = Portfolio([a, inner], name="book")
    flat = p.scale(3, in_place=False)
    assert flat.name is None
    assert len(flat) == 2  # flattened: no nested Portfolio child
    assert all(x.quantity_ == 3.0 for x in flat.priceables)
    # the originals are untouched
    assert a.quantity_ == 1.0


# --------------------------------------------------------------------------------- to_frame


def test_to_frame_shape_and_name_column():
    a = _resolved_swap("a", "10y")
    b = _resolved_swap("b", "5y")
    p = Portfolio([a, b], name="book")
    df = p.to_frame()
    assert "name" in df.columns
    assert list(df.columns[:2]) == ["asset_class", "type"]
    assert list(df.columns[2:]) == sorted(df.columns[2:])
    assert df.index.names == ["portfolio", "instrument"]
    assert len(df) == 2


def test_to_frame_mappings_str_alias_and_callable():
    """gs's `mappings` param: a str value aliases an existing column under a new name, a callable
    is applied row-wise (verified against gs_quant markets/portfolio.py Portfolio.to_frame)."""
    a = _resolved_swap("a", "10y")
    p = Portfolio([a], name="book")
    df = p.to_frame(mappings={"name_alias": "name", "double_qty": lambda row: row["quantity_"] * 2})
    assert (df["name_alias"] == df["name"]).all()
    assert (df["double_qty"] == df["quantity_"] * 2).all()


def test_to_frame_uses_parent_portfolio_name():
    a = _resolved_swap("a", "10y")
    inner = Portfolio([a], name="inner")
    outer = Portfolio([inner], name="outer")
    df = outer.to_frame()
    assert df.index.get_level_values("portfolio")[0] == "inner"
