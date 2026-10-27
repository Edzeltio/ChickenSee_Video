Set WshShell = CreateObject("WScript.Shell")
Set FileSystem = CreateObject("Scripting.FileSystemObject")
AgentDir = FileSystem.GetParentFolderName(WScript.ScriptFullName)
WshShell.Run chr(34) & AgentDir & "\start-agent.bat" & chr(34), 0
Set WshShell = Nothing
Set FileSystem = Nothing