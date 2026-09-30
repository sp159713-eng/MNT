@echo off
powershell -NoProfile -Command "$a = New-ScheduledTaskAction -Execute 'py' -Argument '-3.13 D:\project\MNT\scripts\bots.py run' -WorkingDirectory 'D:\project\MNT'; $t = New-ScheduledTaskTrigger -Daily -At 16:30; $s = New-ScheduledTaskSettingsSet -StartWhenAvailable; Register-ScheduledTask -TaskName 'MNT_PaperBots' -Action $a -Trigger $t -Settings $s -Force"
