@echo off
cd /d C:\Users\LHDA\Documents\Code\Video-Dewatermark-and-Upscale
C:\Python312\python.exe -m uvicorn app_api:app --host 0.0.0.0 --port 8288 --log-level info
