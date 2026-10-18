with open("workflows/portfolio_daily_report.py", "r") as f:
    content = f.read()

# Replace actual newlines inside strings with literal \n
content = content.replace('lines.append("*Not enough historical snapshots — need at least 2 days of data.*\n")', 'lines.append("*Not enough historical snapshots — need at least 2 days of data.*\\n")')
content = content.replace('r_lines.append(f"*{alloc_pct:.1f}% of Portfolio Value · sorted by total move (fallers first)*\\n")', 'r_lines.append(f"*{alloc_pct:.1f}% of Portfolio Value · sorted by total move (fallers first)*\\\\n")')

with open("workflows/portfolio_daily_report.py", "w") as f:
    f.write(content)
