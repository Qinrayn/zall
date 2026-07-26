import math, json

def solve(n, max_xy_ratio=10):
    """Bounded search for 4/n = 1/x + 1/y + 1/z with x <= y <= z."""
    # 4/n = 1/x + 1/y + 1/z  =>  x > n/4
    x_min = n // 4 + 1
    x_max = n  # sensible bound
    for x in range(x_min, x_max + 1):
        # 4/n - 1/x = 1/y + 1/z = (4*x - n) / (n*x)
        num = 4 * x - n
        den = n * x
        if num <= 0:
            continue
        # 1/y + 1/z = num/den  =>  y > den/num
        y_min = den // num + 1
        # avoid absurdly large y: y <= x * max_xy_ratio
        y_max = min(x * max_xy_ratio, den)
        for y in range(y_min, y_max + 1):
            # 1/z = num/den - 1/y = (num*y - den) / (den*y)
            num2 = num * y - den
            den2 = den * y
            if num2 <= 0:
                continue
            if den2 % num2 != 0:
                continue
            z = den2 // num2
            if z < y:
                continue
            # Integer verification: 4*x*y*z == n*(y*z + x*z + x*y)
            if 4 * x * y * z == n * (y * z + x * z + x * y):
                return (x, y, z)
    return None

if __name__ == '__main__':
    import time
    start = time.time()
    counter = 0
    with open('es_results.jsonl', 'w') as f:
        for n in range(2, 20001):
            result = solve(n)
            if result is None:
                counter += 1
                f.write(json.dumps({'n': n, 'none': True}) + '\n')
            else:
                x, y, z = result
                f.write(json.dumps({'n': n, 'x': x, 'y': y, 'z': z, 'ok': True}) + '\n')
    elapsed = time.time() - start
    print(f'Done. Swept n=2..20000. Counterexamples: {counter}. Time: {elapsed:.2f}s')