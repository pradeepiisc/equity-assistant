# Just for analytical context, looking at how the AI/Defense/EMS themes are playing out
import json

def get_sector_allocations(user_id):
    try:
        with open(f"portfolio/{user_id}/sector_allocation.md") as f:
            lines = f.readlines()
            for line in lines:
                if "Aerospace" in line or "Defense" in line or "EMS" in line or "Capital Goods" in line or "Capital" in line:
                    print(line.strip())
    except:
        pass

print("--- ZV3899 Thematic Allocations ---")
get_sector_allocations("ZV3899")
print("--- WN5759 Thematic Allocations ---")
get_sector_allocations("WN5759")
