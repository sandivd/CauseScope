# Coverage.py's documented partial-branch example; no external source changes.
def my_partial_fn(x):
    if x:
        y = 10
    return y


my_partial_fn(1)
