' ===========================================================================
'  Collection Database - no-console front door (Windows)
'
'  Double-click this file to start the app with NO console window. It runs
'  Start-Collection-Hidden.bat fully hidden, which activates the conda env and
'  starts the server (pythonw, no console). Closing the app window quits the
'  server, with a desktop notification — nothing is left running invisibly.
'
'  If the 'collection' environment cannot be activated the batch returns a
'  non-zero code and this shows an error box, so the failure is never silent.
' ===========================================================================
Option Explicit
Dim sh, fso, here, bat, rc
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
bat = """" & here & "\Start-Collection-Hidden.bat" & """"

' Run hidden (0) and wait (True) so we can read the batch's exit code.
rc = sh.Run("cmd /c " & bat, 0, True)

If rc <> 0 Then
    MsgBox "Collection could not start: the 'collection' Anaconda/Miniconda " & _
           "environment was not found." & vbCrLf & vbCrLf & _
           "Open an Anaconda Prompt and run once:" & vbCrLf & _
           "    conda env create -f environment.yml" & vbCrLf & _
           "    conda activate collection", _
           vbExclamation, "Collection Database"
End If
