from pathlib import Path
import json

companies_dir = Path('data/companies')
companies = sorted([d for d in companies_dir.iterdir() if d.is_dir()])

summary = []
for co in companies:
    sym = co.name
    has_insights = any(co.glob('insights/*_insights_*.txt'))
    has_financials = any(co.glob('financials/*_financials_*.txt'))
    has_shareholding = any(co.glob('shareholding/*_shareholding_*.txt'))
    n_transcripts = len(list(co.glob('transcripts/*.pdf')))
    n_ppt = len(list(co.glob('ppt/*.pdf')))
    summary.append({
        'sym': sym,
        'ins': 'Y' if has_insights else '-',
        'fin': 'Y' if has_financials else '-',
        'shr': 'Y' if has_shareholding else '-',
        'tr': n_transcripts,
        'ppt': n_ppt,
    })

print(f"{'SYM':<14} INS FIN SHR TR  PPT")
print('-'*40)
for s in summary:
    missing = []
    if s['ins']=='-': missing.append('ins')
    if s['fin']=='-': missing.append('fin')
    if s['shr']=='-': missing.append('shr')
    if s['tr']<4: missing.append(f'tr({s["tr"]})')
    if s['ppt']<4: missing.append(f'ppt({s["ppt"]})')
    flag = '  MISSING: ' + ','.join(missing) if missing else ''
    print(f'{s["sym"]:<14} {s["ins"]}   {s["fin"]}   {s["shr"]}   {s["tr"]}   {s["ppt"]}{flag}')

n_no_ins = sum(1 for s in summary if s['ins']=='-')
n_no_fin = sum(1 for s in summary if s['fin']=='-')
n_no_shr = sum(1 for s in summary if s['shr']=='-')
n_no_tr  = sum(1 for s in summary if s['tr']<4)
n_no_ppt = sum(1 for s in summary if s['ppt']<4)
print()
print(f'Total companies: {len(summary)}')
print(f'Missing insights:     {n_no_ins}')
print(f'Missing financials:   {n_no_fin}')
print(f'Missing shareholding: {n_no_shr}')
print(f'Transcripts < 4:      {n_no_tr}')
print(f'PPTs < 4:             {n_no_ppt}')