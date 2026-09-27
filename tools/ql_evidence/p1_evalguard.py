"""Does restoring the evaluation date throw when bootstrap helpers/curves are alive? Is the date still restored? Do objects die on del?"""
import sys, gc
sys.path.insert(0, "tools/ql_evidence")
from qlladder import *

hol = load_cal("nyc"); cal = ql_calendar(hol)
ref = dt.date(2024, 6, 12)
nodes = list(zip(*load_row(ref)))
S = ql.Settings.instance()
orig = S.evaluationDate
print("orig eval date", orig)

def build():
    lad = QLLadder(nodes, cal, ["2Y", "5Y", "10Y"])
    return lad

S.evaluationDate = qd(ref)
lad = build()
try:
    S.evaluationDate = orig
    print("restore ok while alive")
except RuntimeError as e:
    print("restore raised:", str(e)[:120])
print("date after the failed restore:", S.evaluationDate, "(restored despite the exception)" if S.evaluationDate == orig else "(NOT restored)")
# now the same with deletion first
S.evaluationDate = qd(ref)
lad = build()
del lad
gc.collect()
try:
    S.evaluationDate = orig
    print("restore ok after del")
except RuntimeError as e:
    print("restore raised after del:", str(e)[:120])
