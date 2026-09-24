' Start DiscSteward without a Command Prompt window.
Option Explicit
Dim shell, files, root
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
shell.Run "pyw -3 """ & root & "\discsteward-ui.py""", 0, False
