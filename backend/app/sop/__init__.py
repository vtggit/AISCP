"""S&OP review: the horizon's projection, spend and forecast accuracy.

``GET /api/sop/review`` (served by ``app.api.sop``) answers with the
site-grouped roll-forward projection of the recommendable portfolio
summed across every site per horizon month, the site codes that run
short, the month's spend against the stored purchase budgets and the
realised accuracy of the stored forecast snapshots — the read the
``app.sop.review`` builder computes in one pass.
"""
