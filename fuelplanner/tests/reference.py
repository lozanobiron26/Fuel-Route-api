"""Reference greedy solver (provably optimal with no stop penalty), used to check the DP."""


def greedy_cost(miles, prices, total, cap):
    """Cheapest cost (in miles-of-fuel * price) starting empty at station 0 located at mile 0."""
    n = len(miles)
    i, fuel, cost = 0, 0.0, 0.0
    while True:
        reach = miles[i] + cap
        cheaper = next(
            (k for k in range(i + 1, n) if miles[k] <= min(reach, total) and prices[k] < prices[i]),
            None,
        )
        if cheaper is not None:
            need = miles[cheaper] - miles[i]
            buy = max(0.0, need - fuel)
            cost += buy * prices[i]
            fuel += buy - need
            i = cheaper
            continue
        if total <= reach:
            cost += max(0.0, total - miles[i] - fuel) * prices[i]
            return cost
        in_range = [k for k in range(i + 1, n) if miles[k] <= reach]
        if not in_range:
            return None
        nxt = min(in_range, key=lambda k: (prices[k], -miles[k]))
        cost += (cap - fuel) * prices[i]
        fuel = cap - (miles[nxt] - miles[i])
        i = nxt
