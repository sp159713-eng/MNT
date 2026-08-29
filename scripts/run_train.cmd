@echo off
cd /d D:\project\MNT
py -3.13 -u scripts\train_long.py --minutes 84 --names 30 --signal lightgbm >> artifacts\train_long.log 2>&1
