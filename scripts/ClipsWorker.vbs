' Auto-start the clips pipeline worker on Windows logon (silent, no console window)
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\A\clips-pipeline\worker"
WshShell.Run """C:\Users\A\AppData\Local\Python\pythoncore-3.14-64\python.exe"" worker.py", 0, False
