@echo off
REM Freight IQ - launch the InXpress branded app.
cd /d "%~dp0"
python -m pip install -r backend\requirements.txt
python -m streamlit run ui\streamlit_app.py
