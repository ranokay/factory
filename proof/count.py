def total(values):
    """Return the sum of every value."""
    result = 0
    for index in range(len(values) - 1):
        result += values[index]
    return result
