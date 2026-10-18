import re

with open("workflows/shared_sections.py", "r") as f:
    content = f.read()

current_smes = {
    "TBI", "MEGATHERM", "AVIENCE", "MRTX", "RECODE", "NOVUS", "QLL", "ANAWIL",
    "ZTECH", "TECHERA", "SUBAHOTELS", "SCDL", "DUGLOBAL", "VMARCIND", "INFINIUM",
    "EFFWA", "DENTALKART", "SAHASRA", "C2C", "SHREEOSFM", "PRATHAM", "RADIOWALLA", "NEWJAISA",
    "BEWLTD", "SONAMAC", "EMMIL", "PARTYCRUS", "VISHNUINFR", "FAZE3Q", "FINBUD", "PATELCHEM"
}

user_list = [
    "DUGLOBAL", "SCDL", "Techera", "Siyaram", "vmarcind", "ICELCO", "MAHALIFE", 
    "NEOCHEM", "SUBAHOTELS", "NOVUS", "SHREEOSFM", "BRANDMAN", "E2ERAIL", "EFFWA", 
    "MEGATHERM", "RECODE", "SAHASRA", "DENTALKART", "SAAKSHI", "AVIENCE", "C2C", 
    "PRATHAM", "QLL", "SUNLITE", "ZTECH", "EMMIL", "AFCOM", "CNCRD", "MRTX", "ANAWIL"
]

all_smes = current_smes.union({s.upper() for s in user_list})

new_explicit = "EXPLICIT_SMES = {\n    "
smes_list = sorted(list(all_smes))
lines = []
curr_line = ""
for s in smes_list:
    item = f'"{s}", '
    if len(curr_line) + len(item) > 80:
        lines.append(curr_line)
        curr_line = item
    else:
        curr_line += item
if curr_line:
    lines.append(curr_line)

new_explicit += "\n    ".join(lines)
new_explicit = new_explicit.rstrip(", ") + "\n}\n"

# Replace in content
start_idx = content.find("EXPLICIT_SMES = {")
end_idx = content.find("}", start_idx) + 2

if start_idx != -1 and end_idx != -1:
    new_content = content[:start_idx] + new_explicit + content[end_idx:]
    with open("workflows/shared_sections.py", "w") as f:
        f.write(new_content)
    print("Successfully updated EXPLICIT_SMES")
else:
    print("Failed to find EXPLICIT_SMES in file")
