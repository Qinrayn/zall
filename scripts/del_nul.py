"""Try to delete the stubborn nul file.

已验证有效方法 (2026-07-26): cmd 的设备路径前缀 \\.\ ——
    cmd /c "del /f \\.\C:\\Users\\云丘\\zall\\nul"
Python os.remove / rename / \\?\ 前缀均报 WinError 5; 只有 cmd 内建 del
走 \\.\ 设备命名空间能绕过保留名检查。本脚本末尾已加 Method 4。
"""
import os
import subprocess

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

# Method 4 (已验证有效): cmd del + \\.\ 设备路径 — 绕过 Win32 保留名解析
try:
    subprocess.run(
        [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                      "System32", "cmd.exe"),
         "/c", f"del /f \\\\.\\{nul_path}"],
        check=True, capture_output=True,
    )
    print("Method 4 SUCCESS: cmd del \\\\.\\ device path")
except Exception as e:
    print(f"Method 4 FAILED: {e}")

# Check result
if os.path.exists(path):
    print(f"Remaining: {os.listdir(path)}")
else:
    print("DIRECTORY FULLY DELETED!")