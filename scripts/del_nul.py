"""Try to delete the stubborn nul file."""
import os

path = r"C:\Users\云丘\zall"
nul_path = os.path.join(path, "nul")
print(f"Path exists: {os.path.exists(path)}")
print(f"Nul exists: {os.path.exists(nul_path)}")

# Method 1: direct remove
try:
    os.remove(nul_path)
    print("Method 1 SUCCESS: direct remove")
except Exception as e:
    print(f"Method 1 FAILED: {e}")

# Method 2: rename first, then delete
try:
    renamed = os.path.join(path, "_old_nul_")
    os.rename(nul_path, renamed)
    print("Method 2 SUCCESS: renamed")
    os.remove(renamed)
    print("  and deleted")
except Exception as e:
    print(f"Method 2 FAILED: {e}")

# Method 3: \\?\ prefix via raw string
try:
    import ntpath
    raw_path = ntpath.join("\\\\?\\", r"C:\Users\云丘\zall\nul")
    os.remove(raw_path)
    print("Method 3 SUCCESS: \\\\?\\ prefix")
except Exception as e:
    print(f"Method 3 FAILED: {e}")

# Check result
if os.path.exists(path):
    print(f"Remaining: {os.listdir(path)}")
else:
    print("DIRECTORY FULLY DELETED!")