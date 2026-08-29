@echo off
cd /d D:\project\MNT
py -3.13 -u scripts\size_sweep.py --draws 300 --minutes 55 --first-seed 6100 --sizes 30,60,120,240 >> artifacts\size_sweep.log 2>&1
