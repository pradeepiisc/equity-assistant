import json

stocks = {}
with open('temp_current.py') as f:
    lines = f.readlines()
    start = False
    for line in lines:
        if line.startswith("CURRENT_STOCKS_LIST"):
            start = True
            continue
        if start and ":" in line:
            parts = line.split(":")
            stocks[parts[0].strip()] = float(parts[1].strip())

with open('temp_exit_table.json') as f:
    exit_table = json.load(f)

overlap = []
for s in stocks:
    # Some symbols might have "-SM" suffix in the exit table
    match = None
    if s in exit_table:
        match = s
    elif s + "-SM" in exit_table:
        match = s + "-SM"
    elif s + "-BE" in exit_table:
        match = s + "-BE"
    
    if match:
        overlap.append((s, match, stocks[s], exit_table[match]))

print(f"Overlap: {len(overlap)} stocks")
# Let's print the first 5
for o in overlap[:5]:
    print(o[0])

# Wait, how many total?
