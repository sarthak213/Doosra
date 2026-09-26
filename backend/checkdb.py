"""Quick sanity check on the built database. Run from the backend/ folder:
    python checkdb.py
"""
import duckdb

# Read-only: this only inspects the database, it should never mutate it.
con = duckdb.connect("data/cricket.duckdb", read_only=True)

print("Matches:", con.execute("SELECT COUNT(*) FROM matches").fetchone()[0])
print("Deliveries:", con.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0])
if con.execute(
    "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'deliveries_wickets'"
).fetchone()[0]:
    print("Wickets:", con.execute("SELECT COUNT(*) FROM deliveries_wickets").fetchone()[0])
else:
    print("Wickets: no deliveries_wickets table (pre-reingest schema)")
print()

print("By format (match_type):")
print(con.execute(
    "SELECT match_type, COUNT(*) FROM matches GROUP BY match_type ORDER BY 2 DESC"
).df())
print()

print("Top tournaments (event_name):")
print(con.execute(
    "SELECT event_name, COUNT(*) FROM matches WHERE event_name IS NOT NULL "
    "GROUP BY event_name ORDER BY 2 DESC LIMIT 15"
).df())

con.close()