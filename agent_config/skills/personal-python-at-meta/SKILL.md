---
name: personal-python-at-meta
description: >-
  Personal augmentation to the python-at-meta skill. Use ALONGSIDE
  python-at-meta whenever writing, running, or debugging Python in fbsource
  and a third-party package is involved. ESPECIALLY use when hitting
  ModuleNotFoundError under fbpython (e.g. "No module named 'numpy'"), or
  when tempted to install a package. Trigger keywords: fbpython
  ModuleNotFoundError, No module named, numpy, pandas, scipy, torch, pip
  install, mgt import, third-party pypi, import fails, package not found,
  how to install a python package at Meta.
---

# Personal: Python third-party packages at Meta

This skill exists alongside the vendored `python-at-meta` skill. It captures a
gotcha that skill states only obliquely: **how to actually use a third-party
package (numpy, pandas, …) and why the "obvious" install commands don't work.**

## GOTCHA — `fbpython` has only the standard library

`fbpython` CANNOT `import` numpy, pandas, or any other `third-party/pypi`
package. A bare `fbpython` REPL or standalone script hitting
`ModuleNotFoundError: No module named 'numpy'` is **expected**, not a broken
environment.

Do **NOT** try to "install" the package to fix this:

- **`pip install`** does not work — Meta Python deps are managed by Buck, not pip.
- **`mgt import`** is ONLY for adding a *brand-new* package that does not yet
  exist under `third-party/pypi/`. numpy, pandas, and virtually every common
  package already exist there, so there is nothing to import — running it just
  fails or wastes time.

The fix is to declare the package as a **Buck dependency** and run via
`buck2 run`.

## Using a third-party package

The dep label is `fbsource//third-party/pypi/<package>:<package>`. Add it to a
`python_binary` / `fb_python_binary` and run that target:

```python
# BUCK
load("@fbcode_macros//build_defs:python_binary.bzl", "python_binary")

oncall("your_team")

python_binary(
    name = "analyze",
    main_module = "analyze",
    srcs = ["analyze.py"],
    deps = [
        "fbsource//third-party/pypi/numpy:numpy",
        "fbsource//third-party/pypi/pandas:pandas",
    ],
)
```

```python
# analyze.py
import numpy as np
import pandas as pd

print(pd.DataFrame({"x": np.arange(5)}))
```

```bash
buck2 run @fbcode//mode/opt //your/dir:analyze
```

## Interactive / scratch data work

For ad-hoc numpy/pandas exploration, prefer a Bento notebook
(`internalfb.com/intern/anp/`) or a prebuilt IPython Buck target over
`fbpython` — the scientific stack is already wired in and there is no BUCK file
to maintain.

## See also

For everything else about Python at Meta (fbpython usage, Buck target triplets,
Pyre, testing, standalone-vs-Buck, third-party version resolution), defer to the
`python-at-meta` skill.
