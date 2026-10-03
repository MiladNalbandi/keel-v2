"""A tiny score table: record scores per player and read them back."""


def average(scores: list[int]) -> float:
    if not scores:
        raise ValueError("no scores")
    return sum(scores) / len(scores)


def top(table: dict[str, int], n: int = 3) -> list[str]:
    return [name for name, _ in sorted(table.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]
