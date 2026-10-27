import getpass
import auth

u = input("Username: ").strip()
p = getpass.getpass("Password (min 8 characters, typing is hidden): ")
r = input("Role (admin or guest): ").strip().lower()

if not u or len(p) < 8 or r not in ("admin", "guest"):
    raise SystemExit("Invalid input. Run the command again.")

auth.create_user(u, p, r)
print(f"Saved user '{u.lower()}' as {r}.")