Set fso = CreateObject("Scripting.FileSystemObject")
On Error Resume Next
fso.DeleteFile "C:\Users\云丘\zall\nul", True
If Err.Number = 0 Then
    WScript.Echo "ok"
Else
    WScript.Echo "fail: " & Err.Description
End If