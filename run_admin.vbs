' Hidden launcher for run_admin.bat -- starts the admin server (if not already running) and opens it,
' with no console window. Same pattern as XZYT\run_admin.vbs. Double-click this file.
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
WshShell.Run "run_admin.bat", 0, False
