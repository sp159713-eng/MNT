@echo off
cd /d D:\project\MNT
py -3.13 -u production.py >> artifacts\refit.log 2>&1
