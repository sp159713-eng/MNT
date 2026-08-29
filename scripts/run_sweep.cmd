@echo off
cd /d D:\project\MNT
py -3.13 -u scripts\size_sweep.py --draws 200 --minutes 70 --first-seed 5001 >> artifacts\size_sweep.log 2>&1
